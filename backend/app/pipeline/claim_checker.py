"""Post-generation Claim Checker — verify report claims against grounded evidence."""
from __future__ import annotations

import math
import re
from enum import Enum

from loguru import logger
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..domain.schemas import Evidence, Formulation
from ..services import llm

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
_MIN_CLAIM_LEN = 20
_MAX_CLAIMS = 20
_PASS_RATE_THRESHOLD = 0.5
_SUPPORTED_OVERLAP = 0.25
_WEAK_OVERLAP = 0.12
# Evidence items actually shown to the LLM in _verify_prompt. Citations must
# be validated against this same slice — checking against the full evidence
# list would accept an index the model was never shown as a valid citation.
_MAX_EVIDENCE_SHOWN = 12


class ClaimVerdict(str, Enum):
    supported = "supported"
    unsupported = "unsupported"
    conflicting = "conflicting"
    insufficient = "insufficient"


class VerifiedClaim(BaseModel):
    text: str
    verdict: ClaimVerdict
    evidence_indices: list[int] = Field(default_factory=list)
    source_tags: list[str] = Field(default_factory=list)
    reason: str = ""


class ClaimCheckResult(BaseModel):
    claims: list[VerifiedClaim] = Field(default_factory=list)
    pass_rate: float = 1.0
    needs_regenerate: bool = False
    claim_check_passed: bool = True
    engine: str = "offline"


def _token_set(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text) if len(t) > 1}


def extract_claims(text: str, *, max_claims: int | None = None) -> list[str]:
    """Pull checkable factual statements from markdown report text."""
    limit = int(max_claims if max_claims is not None else _MAX_CLAIMS)
    claims: list[str] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if raw.startswith(("- ", "* ", "1.", "2.", "3.", "4.", "5.")):
            raw = re.sub(r"^[\-\*]\s+|^\d+\.\s+", "", raw).strip()
        if len(raw) < _MIN_CLAIM_LEN:
            continue
        if raw.startswith("**") and raw.endswith("**") and len(raw) < 40:
            continue
        claims.append(raw[:600])
    if not claims:
        for para in text.split("\n\n"):
            p = para.strip()
            if len(p) >= _MIN_CLAIM_LEN and not p.startswith("#"):
                claims.append(p[:600])
    return claims[: max(1, limit)]


_SECTION_HEADING_RE = re.compile(r"^#{1,3}\s+", re.MULTILINE)


def extract_claims_by_section(
    text: str,
    *,
    per_section: int = 8,
    max_total: int | None = None,
) -> list[str]:
    """Wave B: sample claims per ``##`` section so long reports are not head-biased."""
    body = text or ""
    parts = _SECTION_HEADING_RE.split(body)
    # split drops headings; first part may be preamble
    sections = [p for p in parts if (p or "").strip()]
    if not sections:
        return extract_claims(body, max_claims=max_total)
    out: list[str] = []
    seen: set[str] = set()
    cap_total = int(max_total if max_total is not None else _MAX_CLAIMS * 2)
    for sec in sections:
        for c in extract_claims(sec, max_claims=per_section):
            key = c[:80]
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
            if len(out) >= cap_total:
                return out
    return out


def _source_tags_for_indices(evidence: list[Evidence], indices: list[int]) -> list[str]:
    tags: list[str] = []
    for idx in indices:
        if 0 <= idx < len(evidence):
            src = evidence[idx].source
            if src and src not in tags:
                tags.append(src)
    return tags


def verify_claim_offline(claim: str, evidence: list[Evidence]) -> VerifiedClaim:
    claim_tokens = _token_set(claim)
    if not claim_tokens:
        return VerifiedClaim(
            text=claim,
            verdict=ClaimVerdict.insufficient,
            reason="empty claim",
        )
    if not evidence:
        return VerifiedClaim(
            text=claim,
            verdict=ClaimVerdict.unsupported,
            reason="no grounded evidence",
        )

    best_idx = -1
    best_score = 0.0
    for i, ev in enumerate(evidence):
        ev_tokens = _token_set(f"{ev.title} {ev.snippet}")
        if not ev_tokens:
            continue
        overlap = len(claim_tokens & ev_tokens) / len(claim_tokens)
        if overlap > best_score:
            best_score = overlap
            best_idx = i

    indices = [best_idx] if best_idx >= 0 else []
    tags = _source_tags_for_indices(evidence, indices)

    if best_score >= _SUPPORTED_OVERLAP:
        return VerifiedClaim(
            text=claim,
            verdict=ClaimVerdict.supported,
            evidence_indices=indices,
            source_tags=tags,
            reason=f"token overlap {best_score:.2f}",
        )
    if best_score >= _WEAK_OVERLAP:
        return VerifiedClaim(
            text=claim,
            verdict=ClaimVerdict.insufficient,
            evidence_indices=indices,
            source_tags=tags,
            reason=f"weak overlap {best_score:.2f}",
        )
    return VerifiedClaim(
        text=claim,
        verdict=ClaimVerdict.unsupported,
        reason="no evidence overlap",
    )


def _verify_prompt(topic: str, claims: list[str], evidence: list[Evidence]) -> str:
    ev_lines = "\n".join(
        f"[{i}] ({e.source}) {e.title}: {e.snippet[:200]}"
        for i, e in enumerate(evidence[:_MAX_EVIDENCE_SHOWN])
    )
    claim_lines = "\n".join(f"{i}. {c}" for i, c in enumerate(claims))
    return (
        "你是研究报告论断核验器。对每条论断，判断是否有给定证据支撑。\n"
        f"研究主题：{topic}\n\n"
        f"证据：\n{ev_lines or '(无)'}\n\n"
        f"待核验论断：\n{claim_lines}\n\n"
        "返回 JSON：\n"
        '{"claims":[{"index":0,"verdict":"supported"|"unsupported"|"conflicting"|"insufficient",'
        '"evidence_indices":[0],"reason":"..."},...]}'
    )


def verify_claims_llm(
    topic: str,
    claims: list[str],
    evidence: list[Evidence],
) -> list[VerifiedClaim]:
    data = llm.complete_json(_verify_prompt(topic, claims, evidence))
    if not isinstance(data, dict):
        raise ValueError("invalid claim check JSON")

    shown_evidence = evidence[:_MAX_EVIDENCE_SHOWN]
    out: list[VerifiedClaim] = []
    for item in data.get("claims") or []:
        try:
            idx = int(item["index"])
            verdict = ClaimVerdict(str(item["verdict"]))
            ev_indices = [
                int(i) for i in (item.get("evidence_indices") or []) if 0 <= int(i) < len(shown_evidence)
            ]
            out.append(
                VerifiedClaim(
                    text=claims[idx] if 0 <= idx < len(claims) else "",
                    verdict=verdict,
                    evidence_indices=ev_indices,
                    source_tags=_source_tags_for_indices(shown_evidence, ev_indices),
                    reason=str(item.get("reason") or ""),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    if len(out) != len(claims):
        raise ValueError("incomplete claim check response")
    return out


def check_claims(
    topic: str,
    report_markdown: str,
    evidence: list[Evidence],
    settings: Settings | None = None,
) -> ClaimCheckResult:
    """Verify report claims against grounded evidence (LLM with offline fallback)."""
    settings = settings or get_settings()
    # Wave B: per-section sampling so long STORM reports are not head-biased.
    claims = extract_claims_by_section(report_markdown, per_section=8, max_total=_MAX_CLAIMS)
    if not claims:
        return ClaimCheckResult(engine="offline")

    engine = "offline"
    verified: list[VerifiedClaim]
    if settings.get_active_api_key():
        try:
            verified = verify_claims_llm(topic, claims, evidence)
            engine = "llm"
        except Exception as exc:
            logger.warning("Claim check LLM failed: {}", exc)
            verified = [verify_claim_offline(c, evidence) for c in claims]
            engine = "degraded"  # 明确区分：曾尝试 LLM 但失败回退
    else:
        verified = [verify_claim_offline(c, evidence) for c in claims]

    supported = sum(1 for v in verified if v.verdict == ClaimVerdict.supported)
    pass_rate = supported / len(verified) if verified else 1.0
    # `insufficient` belongs here alongside unsupported/conflicting: it is the
    # same "flagged" set append_verification_footer surfaces to the reader.
    # Without it, a report where every claim lands on weak token overlap gets
    # pass_rate=0.0 but needs_regenerate=False, which the `or not
    # needs_regenerate` below turns into claim_check_passed=True — a 0%
    # confirmed report marked "verified" with no footer and no regeneration.
    needs_regenerate = any(
        v.verdict in (ClaimVerdict.unsupported, ClaimVerdict.conflicting, ClaimVerdict.insufficient)
        for v in verified
    )
    passed = pass_rate >= _PASS_RATE_THRESHOLD or not needs_regenerate

    return ClaimCheckResult(
        claims=verified,
        pass_rate=round(pass_rate, 4),
        needs_regenerate=needs_regenerate and not passed,
        claim_check_passed=passed,
        engine=engine,
    )


def append_verification_footer(report: str, result: ClaimCheckResult) -> str:
    """Append a verification summary for failed / weak claims (fail-open)."""
    flagged = [
        v
        for v in result.claims
        if v.verdict in (ClaimVerdict.unsupported, ClaimVerdict.conflicting, ClaimVerdict.insufficient)
    ]
    if not flagged:
        return report
    lines = ["", "## 论断核验（Claim Checker）", ""]
    for v in flagged[:10]:
        lines.append(f"- **{v.verdict.value}**: {v.text[:240]}")
        if v.reason:
            lines.append(f"  - {v.reason}")
    lines.append("")
    lines.append(f"核验通过率: {result.pass_rate:.0%}（引擎: {result.engine}）")
    return report.rstrip() + "\n" + "\n".join(lines)


def regenerate_prompt(topic: str, answer: str, failed_claims: list[VerifiedClaim]) -> str:
    """Build a narrowed rewrite prompt for unsupported claims."""
    failed_text = "\n".join(f"- {v.text}" for v in failed_claims[:8])
    return (
        f"{answer}\n\n"
        "【需修正的缺乏证据支撑的论断】\n"
        f"{failed_text}\n\n"
        "请基于可引用证据重写上述报告，对无法证实的部分必须标注「证据不足」。"
        f"研究主题：{topic}"
    )


_NUMERIC_IN_TEXT = re.compile(r"(\d+(?:\.\d+)?)")


# B-4: metric 名称后缀 → numeric_check 单位提示。predicted 字典里是裸 float，
# 靠后缀把预测值配成 (值, 单位) 对，再用共享的 extract_numbers/_to_base 做
# unit-aware 的证据匹配（与 P2-1 chat 数值门同一套实现）。
_METRIC_UNIT_HINTS: tuple[tuple[str, str], ...] = (
    ("_hours", "h"),
    ("_hour", "h"),
    ("_mpa", "mpa"),
    ("_kpa", "kpa"),
    ("_pa", "pa"),
    ("_pct", "pct"),
    ("_percent", "pct"),
    ("_gsm", "gsm"),
    ("_mm", "mm"),
    ("_um", "um"),
    ("_mg", "mg"),
    ("_kg", "kg"),
    ("_g", "g"),
    ("_ml", "ml"),
    ("_min", "min"),
    ("_ph", "ph"),
    ("_c", "c"),
)


def _metric_unit(metric: str) -> str | None:
    m = (metric or "").lower()
    for suffix, unit in sorted(_METRIC_UNIT_HINTS, key=lambda x: -len(x[0])):
        if m.endswith(suffix):
            return unit
    return None


def _evidence_supports_value(evidence: list[Evidence], metric: str, value: float) -> bool:
    """B-4: unit-aware evidence support via the shared numeric_check module.

    Evidence numbers come from ``extract_numbers`` (unit-aware, with unit
    conversions); the predicted bare float is paired with a unit hint derived
    from the metric-name suffix. A match means the same base unit and within
    ±25% — prediction tolerance, since predictions are estimates (unlike the
    strict answer-number gate in chat, which demands exact match).
    Metrics without a unit hint fall back to bare-number ±25% (old behavior).
    """
    from ..services.numeric_check import _to_base, extract_numbers

    if not evidence or value <= 0:
        return bool(evidence)
    metric_tokens = _token_set(metric.replace("_", " "))
    unit = _metric_unit(metric)
    pred_base, pred_v = _to_base(value, unit) if unit else (None, value)
    for ev in evidence[:12]:
        blob = f"{ev.title} {ev.snippet}".lower()
        if metric_tokens and not (metric_tokens & _token_set(blob)):
            continue
        for ev_v, ev_u in extract_numbers(f"{ev.title} {ev.snippet}"):
            if unit:
                try:
                    ev_base, ev_bv = _to_base(ev_v, ev_u)
                except Exception:  # noqa: BLE001 - fail-open on odd units
                    continue
                if ev_base == pred_base and math.isclose(
                    ev_bv, pred_v, rel_tol=0.25
                ):
                    return True
            elif ev_v > 0 and abs(ev_v - value) / max(ev_v, value) <= 0.25:
                return True
    return False


_DOWNGRADE_MARKER = "【数值降级】"


def check_formulation_predictions(
    form: Formulation,
    evidence: list[Evidence],
) -> list[str]:
    """Lightweight numeric claim check for recommend-path formulations.

    B-4: evidence matching is unit-aware via the shared ``numeric_check``
    module (same implementation as the P2-1 chat gate). When a predicted
    metric lacks evidence support the prediction is *downgraded*: a structured
    ``【数值降级】`` marker is recorded on ``form.warnings`` (per-formulation
    channel, travels with the scored bundle) in addition to the returned
    bundle-level warning string.
    """
    warnings: list[str] = []
    if not form.predicted:
        return warnings

    rationale = form.rationale or ""
    unsupported: list[str] = []
    for metric, predicted in form.predicted.items():
        if not isinstance(predicted, (int, float)):
            continue
        val = float(predicted)
        if val <= 0:
            continue
        if metric.replace("_", " ") in rationale.lower() or metric in rationale:
            nums = [float(m) for m in _NUMERIC_IN_TEXT.findall(rationale) if float(m) > 0]
            if nums and not any(abs(val - n) / max(val, n) <= 0.2 for n in nums):
                warnings.append(
                    f"{form.name}: rationale numbers disagree with predicted {metric}={val:.2g}"
                )
        if not _evidence_supports_value(evidence, metric, val):
            unsupported.append(f"{metric}={val:.2g}")
    # A re-check replaces the previous verdict instead of stacking a second marker.
    form.warnings = [w for w in form.warnings if not w.startswith(_DOWNGRADE_MARKER)]
    if unsupported:
        # One line per formulation, not one per metric: the predictor emits ~15 metrics and
        # evidence rarely quotes each, so a recipe used to carry ~15 near-identical warnings
        # (times every candidate in the bundle) that buried the ones that matter.
        listed = ", ".join(unsupported)
        warnings.append(
            f"{form.name}: {len(unsupported)} predicted value(s) lack supporting evidence ({listed})"
        )
        form.warnings.append(
            f"{_DOWNGRADE_MARKER}{len(unsupported)} 项预测值在所引证据中无来源（{listed}），"
            "可信度已降级，请谨慎采信"
        )
    return warnings


"""Post-generation Claim Checker — verify report claims against grounded evidence."""
from __future__ import annotations

from ..services.errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal
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


def extract_claims(text: str) -> list[str]:
    """Pull checkable factual statements from markdown report text."""
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
    return claims[:_MAX_CLAIMS]


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
        for i, e in enumerate(evidence[:12])
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

    out: list[VerifiedClaim] = []
    for item in data.get("claims") or []:
        try:
            idx = int(item["index"])
            verdict = ClaimVerdict(str(item["verdict"]))
            ev_indices = [int(i) for i in (item.get("evidence_indices") or [])]
            out.append(
                VerifiedClaim(
                    text=claims[idx] if 0 <= idx < len(claims) else "",
                    verdict=verdict,
                    evidence_indices=ev_indices,
                    source_tags=_source_tags_for_indices(evidence, ev_indices),
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
    claims = extract_claims(report_markdown)
    if not claims:
        return ClaimCheckResult(engine="offline")

    engine = "offline"
    verified: list[VerifiedClaim]
    if settings.get_active_api_key():
        try:
            verified = verify_claims_llm(topic, claims, evidence)
            engine = "llm"
        except Exception as exc:
            logger.warning("Claim check LLM failed: %s", exc)
            verified = [verify_claim_offline(c, evidence) for c in claims]
    else:
        verified = [verify_claim_offline(c, evidence) for c in claims]

    supported = sum(1 for v in verified if v.verdict == ClaimVerdict.supported)
    pass_rate = supported / len(verified) if verified else 1.0
    needs_regenerate = any(
        v.verdict in (ClaimVerdict.unsupported, ClaimVerdict.conflicting) for v in verified
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


def _evidence_supports_value(evidence: list[Evidence], metric: str, value: float) -> bool:
    """Heuristic: evidence snippets mention a numeric value within ±25% of prediction."""
    if not evidence or value <= 0:
        return bool(evidence)
    lo, hi = value * 0.75, value * 1.25
    metric_tokens = _token_set(metric.replace("_", " "))
    for ev in evidence[:12]:
        blob = f"{ev.title} {ev.snippet}".lower()
        if metric_tokens and not (metric_tokens & _token_set(blob)):
            continue
        for match in _NUMERIC_IN_TEXT.findall(blob):
            try:
                num = float(match)
            except ValueError:
                continue
            if lo <= num <= hi:
                return True
    return False


def check_formulation_predictions(
    form: Formulation,
    evidence: list[Evidence],
) -> list[str]:
    """Lightweight numeric claim check for recommend-path formulations."""
    warnings: list[str] = []
    if not form.predicted:
        return warnings

    rationale = form.rationale or ""
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
            warnings.append(
                f"{form.name}: predicted {metric}={val:.2g} lacks supporting evidence"
            )
    return warnings


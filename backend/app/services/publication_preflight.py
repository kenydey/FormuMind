"""Publication preflight — citation / placeholder / numeric gates before export.

Aligned with SynSci ``file/review.ts`` subset; FormuMind uses ``[^n]`` binders.
Chat stays fail-open; Wiki STORM **export** requires assertReady when enabled.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .citation_binder import (
    _CITATION_REF_RE,
    _FOOTNOTE_DEF_RE,
    extract_citation_indices_validated,
)

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()

Kind = Literal["storm", "dossier"]
Severity = Literal["blocking", "major", "minor", "info"]
FindingStatus = Literal["open", "resolved", "overridden"]

_PLACEHOLDER_RE = re.compile(
    r"\[citation needed\]|\bTODO\b|\bTBD\b|待补证据|待补充引用",
    re.I,
)
_NUMERIC_LINE_RE = re.compile(
    r"(\d+(?:\.\d+)?\s*(?:wt%|%|℃|°C|MPa|μm|µm|hrs?|h|min)|"
    r"p\s*[<=>]\s*\d)",
    re.I,
)
# Footnote def body may carry "pp. 3" / "P3" / "¶2" from CitationAnchor.to_citation_text.
_FOOTNOTE_PAGE_RE = re.compile(r"(?:pp?\.\s*|P)(\d+)", re.I)
_FOOTNOTE_PARA_RE = re.compile(r"¶\s*(\d+)")
_INLINE_CITE_RE = re.compile(r"\[\^(\d+)\]")


@dataclass
class Finding:
    id: str
    check: str  # citation | placeholder | numeric
    severity: Severity
    status: FindingStatus
    title: str
    detail: str
    evidence: list[str] = field(default_factory=list)
    location: dict[str, Any] = field(default_factory=dict)
    resolution: dict[str, Any] | None = None


@dataclass
class PreflightState:
    project_id: str
    kind: str
    content_hash: str = ""
    findings: list[Finding] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    finalization: dict[str, Any] | None = None
    updated_at: float = 0.0


def _data_root() -> Path:
    return Path("./data").resolve()


def _state_path(project_id: str, kind: str) -> Path:
    safe = re.sub(r"[^\w.\-]+", "_", project_id.strip())[:120] or "unknown"
    return _data_root() / "preflight" / safe / f"{kind}.json"


def content_hash(markdown: str) -> str:
    return hashlib.sha256((markdown or "").encode("utf-8")).hexdigest()


def preflight_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "publication_preflight_enabled", True))


def _defined_footnote_indices(markdown: str) -> set[int]:
    return {int(m.group(1)) for m in _FOOTNOTE_DEF_RE.finditer(markdown or "")}


def _infer_total_anchors(markdown: str, total_anchors: int | None) -> int:
    if total_anchors is not None and total_anchors >= 0:
        return int(total_anchors)
    defined = _defined_footnote_indices(markdown)
    if defined:
        return max(defined)
    # Fall back to max cited index so only clearly invented high numbers fail
    cited = [int(x) for x in _CITATION_REF_RE.findall(markdown or "")]
    return max(cited) if cited else 0


def _footnote_locator_map(markdown: str) -> dict[int, dict[str, int | None]]:
    """Parse footnote definitions for page/paragraph locators (best-effort).

    ``_FOOTNOTE_DEF_RE`` only matches the ``[^n]: `` prefix; read the rest of
    that line for ``pp. N`` / ``¶N``.
    """
    out: dict[int, dict[str, int | None]] = {}
    text = markdown or ""
    for m in _FOOTNOTE_DEF_RE.finditer(text):
        try:
            n = int(m.group(1))
        except (TypeError, ValueError):
            continue
        # Remainder of the definition line after the matched prefix.
        line_end = text.find("\n", m.end())
        rest = text[m.end() : line_end if line_end >= 0 else None]
        page_m = _FOOTNOTE_PAGE_RE.search(rest)
        para_m = _FOOTNOTE_PARA_RE.search(rest)
        out[n] = {
            "page": int(page_m.group(1)) if page_m else None,
            "paragraph": int(para_m.group(1)) if para_m else None,
        }
    return out


def _locator_preflight_mode(settings: Any | None) -> str:
    """Resolve off | warning | blocking from bool flags (EnvFlag is bool-only).

    Also accepts legacy string ``citation_locator_preflight`` for tests/compat.
    """
    if settings is None:
        return "warning"
    legacy = getattr(settings, "citation_locator_preflight", None)
    if isinstance(legacy, str) and legacy.strip():
        mode = legacy.strip().lower()
        if mode in ("off", "false", "0", "none"):
            return "off"
        if mode in ("blocking", "block", "error"):
            return "blocking"
        if mode in ("warning", "warn", "major"):
            return "warning"
    if not bool(getattr(settings, "citation_locator_preflight_enabled", True)):
        return "off"
    if bool(getattr(settings, "citation_locator_preflight_blocking", False)):
        return "blocking"
    return "warning"


def run_checks(
    markdown: str,
    *,
    total_anchors: int | None = None,
    settings: Any | None = None,
) -> list[Finding]:
    """Pure checks → open findings (no persistence)."""
    text = markdown or ""
    findings: list[Finding] = []
    body = text
    # Prefer checking body without footnote defs for numeric/placeholder noise
    body_wo_defs = _FOOTNOTE_DEF_RE.sub("", text)

    anchors_n = _infer_total_anchors(text, total_anchors)
    defined = _defined_footnote_indices(text)
    valid, oor = extract_citation_indices_validated(body_wo_defs, anchors_n or 10**9)

    # Out of range vs declared total
    if total_anchors is not None and total_anchors >= 0:
        _, oor2 = extract_citation_indices_validated(body_wo_defs, total_anchors)
        for n in oor2:
            findings.append(
                Finding(
                    id=uuid.uuid4().hex[:12],
                    check="citation",
                    severity="blocking",
                    status="open",
                    title=f"引用 [^{n}] 超出锚点范围",
                    detail=f"total_anchors={total_anchors}",
                    evidence=[f"[^{n}]"],
                    location={"line": _line_of(body_wo_defs, f"[^{n}]")},
                )
            )
    else:
        # Without explicit total: refs with no footnote definition are blocking
        for n in valid + oor:
            if n not in defined and defined:
                findings.append(
                    Finding(
                        id=uuid.uuid4().hex[:12],
                        check="citation",
                        severity="blocking",
                        status="open",
                        title=f"引用 [^{n}] 无脚注定义",
                        detail="正文引用了未定义的脚注",
                        evidence=[f"[^{n}]"],
                        location={"line": _line_of(body_wo_defs, f"[^{n}]")},
                    )
                )
            elif not defined and (valid or oor):
                # Citations present but no definitions at all
                pass
        if (valid or oor) and not defined:
            findings.append(
                Finding(
                    id=uuid.uuid4().hex[:12],
                    check="citation",
                    severity="blocking",
                    status="open",
                    title="存在 [^n] 引用但无任何脚注定义",
                    detail=f"cited={sorted(set(valid + oor))}",
                    evidence=[f"[^{n}]" for n in sorted(set(valid + oor))[:8]],
                    location={"line": 1},
                )
            )

    for m in _PLACEHOLDER_RE.finditer(body_wo_defs):
        findings.append(
            Finding(
                id=uuid.uuid4().hex[:12],
                check="placeholder",
                severity="blocking",
                status="open",
                title="残留占位符",
                detail=m.group(0),
                evidence=[m.group(0)],
                location={"line": body_wo_defs[: m.start()].count("\n") + 1},
            )
        )

    for i, line in enumerate(body_wo_defs.splitlines(), start=1):
        if not _NUMERIC_LINE_RE.search(line):
            continue
        if "[^" in line or "doi.org" in line.lower():
            continue
        # skip pure TOC / heading-only lines without claims
        if line.strip().startswith("#"):
            continue
        hit = _NUMERIC_LINE_RE.search(line)
        findings.append(
            Finding(
                id=uuid.uuid4().hex[:12],
                check="numeric",
                severity="major",
                status="open",
                title="数值行缺少内联引用",
                detail=(line.strip()[:160]),
                evidence=[hit.group(0) if hit else ""],
                location={"line": i},
            )
        )

    # Wave D — locator honesty: numeric + [^n] but footnote has no page/¶.
    loc_mode = _locator_preflight_mode(settings)
    if loc_mode != "off":
        severity: Severity = "blocking" if loc_mode == "blocking" else "major"
        loc_map = _footnote_locator_map(text)
        for i, line in enumerate(body_wo_defs.splitlines(), start=1):
            if not _NUMERIC_LINE_RE.search(line):
                continue
            if line.strip().startswith("#"):
                continue
            cites = [int(x) for x in _INLINE_CITE_RE.findall(line)]
            if not cites:
                continue
            missing = []
            for n in cites:
                loc = loc_map.get(n) or {}
                if loc.get("page") is None and loc.get("paragraph") is None:
                    missing.append(n)
            if not missing:
                continue
            findings.append(
                Finding(
                    id=uuid.uuid4().hex[:12],
                    check="locator_missing",
                    severity=severity,
                    status="open",
                    title="数值引用缺少页码/段落 locator",
                    detail=(line.strip()[:160]),
                    evidence=[f"[^{n}]" for n in missing[:8]],
                    location={"line": i},
                )
            )

    return findings


def _line_of(text: str, needle: str) -> int:
    idx = text.find(needle)
    if idx < 0:
        return 1
    return text[:idx].count("\n") + 1


def _load_state(project_id: str, kind: str) -> PreflightState:
    path = _state_path(project_id, kind)
    if not path.is_file():
        return PreflightState(project_id=project_id, kind=kind)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        findings = [Finding(**f) for f in (raw.get("findings") or [])]
        return PreflightState(
            project_id=project_id,
            kind=kind,
            content_hash=str(raw.get("content_hash") or ""),
            findings=findings,
            events=list(raw.get("events") or []),
            finalization=raw.get("finalization"),
            updated_at=float(raw.get("updated_at") or 0),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("preflight state load failed: %s", exc)
        return PreflightState(project_id=project_id, kind=kind)


def _save_state(state: PreflightState) -> None:
    path = _state_path(state.project_id, state.kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = time.time()
    payload = {
        "project_id": state.project_id,
        "kind": state.kind,
        "content_hash": state.content_hash,
        "findings": [asdict(f) for f in state.findings],
        "events": state.events[-200:],
        "finalization": state.finalization,
        "updated_at": state.updated_at,
    }
    with _LOCK:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def review_markdown(
    project_id: str,
    kind: str,
    markdown: str,
    *,
    total_anchors: int | None = None,
    cite_source_ids: list[str] | None = None,
    settings: Any | None = None,
) -> dict[str, Any]:
    from ..config import get_settings

    s = settings or get_settings()
    findings = run_checks(markdown, total_anchors=total_anchors, settings=s)
    # Wave B: merge frozen-corpus / screening findings
    try:
        from .literature_manifest import preflight_corpus_findings

        for raw in preflight_corpus_findings(
            project_id,
            markdown,
            settings=s,
            cite_source_ids=cite_source_ids,
        ):
            findings.append(Finding(**raw))
    except Exception as exc:  # noqa: BLE001
        logger.debug("corpus preflight merge skipped: %s", exc)
    state = PreflightState(
        project_id=project_id,
        kind=kind,
        content_hash=content_hash(markdown),
        findings=findings,
        events=[
            {
                "type": "generated",
                "actor": "system",
                "at": time.time(),
                "count": len(findings),
            }
        ],
        finalization=None,
    )
    _save_state(state)
    return state_to_dict(state)


def override_finding(
    project_id: str,
    kind: str,
    finding_id: str,
    *,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    actor = (actor or "").strip()
    reason = (reason or "").strip()
    if not actor or not reason:
        raise ValueError("override 需要 actor 与 reason")
    state = _load_state(project_id, kind)
    hit = next((f for f in state.findings if f.id == finding_id), None)
    if hit is None:
        raise LookupError("finding not found")
    hit.status = "overridden"
    hit.resolution = {
        "kind": "overridden",
        "actor": actor,
        "reason": reason,
        "at": time.time(),
    }
    state.events.append(
        {
            "type": "overridden",
            "actor": actor,
            "at": time.time(),
            "findingID": finding_id,
            "reason": reason,
        }
    )
    state.finalization = None
    _save_state(state)
    return state_to_dict(state)


def assert_ready(
    project_id: str,
    kind: str,
    markdown: str,
    *,
    actor: str = "system",
) -> dict[str, Any]:
    """Five-step style gate: hash match + no open blocking → finalize."""
    state = _load_state(project_id, kind)
    h = content_hash(markdown)
    errors: list[str] = []
    if not state.findings and not state.content_hash:
        # Auto-review if never reviewed
        review_markdown(project_id, kind, markdown)
        state = _load_state(project_id, kind)
    if state.content_hash and state.content_hash != h:
        errors.append("内容 hash 已变，请重新 review")
    open_blocking = [
        f for f in state.findings if f.severity == "blocking" and f.status == "open"
    ]
    if open_blocking:
        errors.append(f"{len(open_blocking)} 条 open blocking findings")
    if errors:
        return {
            "ok": False,
            "ready": False,
            "errors": errors,
            "state": state_to_dict(state),
        }
    state.finalization = {
        "actor": actor or "system",
        "at": time.time(),
        "artifactHash": h,
    }
    state.events.append(
        {"type": "finalized", "actor": actor or "system", "at": time.time()}
    )
    _save_state(state)
    return {"ok": True, "ready": True, "errors": [], "state": state_to_dict(state)}


def _merge_corpus_into_state(
    state: PreflightState,
    markdown: str,
    *,
    settings: Any,
) -> PreflightState:
    """Refresh Wave B corpus findings without wiping human overrides."""
    try:
        from .literature_manifest import preflight_corpus_findings

        fresh = preflight_corpus_findings(
            state.project_id, markdown, settings=settings
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("corpus merge skipped: %s", exc)
        return state
    # Drop previous open corpus/* findings; keep overrides / other checks
    kept = [
        f
        for f in state.findings
        if f.check not in {"corpus", "corpus_cite", "unscreened_open"}
        or f.status == "overridden"
    ]
    for raw in fresh:
        # Skip if same check+detail already overridden
        if any(
            f.check == raw["check"]
            and f.detail == raw.get("detail")
            and f.status == "overridden"
            for f in state.findings
        ):
            continue
        kept.append(Finding(**raw))
    state.findings = kept
    _save_state(state)
    return state


def export_allowed(
    project_id: str,
    kind: str,
    markdown: str,
    *,
    settings: Any,
) -> tuple[bool, dict[str, Any]]:
    """Return (allowed, detail). When flag off → always allow."""
    if not preflight_enabled(settings):
        return True, {"skipped": True, "reason": "publication_preflight_enabled=false"}
    state = _load_state(project_id, kind)
    h = content_hash(markdown)
    if not state.content_hash or state.content_hash != h:
        review_markdown(project_id, kind, markdown, settings=settings)
        state = _load_state(project_id, kind)
    else:
        # Content unchanged: refresh corpus gates; preserve overrides.
        state = _merge_corpus_into_state(state, markdown, settings=settings)
    open_blocking = [
        f for f in state.findings if f.severity == "blocking" and f.status == "open"
    ]
    if open_blocking:
        return False, {
            "ok": False,
            "ready": False,
            "errors": [f"{len(open_blocking)} open blocking findings"],
            "state": state_to_dict(state),
        }
    # Finalize lazily on successful export gate
    if not state.finalization or state.finalization.get("artifactHash") != h:
        assert_ready(project_id, kind, markdown, actor="export")
        state = _load_state(project_id, kind)
    return True, {"ok": True, "ready": True, "state": state_to_dict(state)}


def get_state(project_id: str, kind: str = "storm") -> dict[str, Any]:
    return state_to_dict(_load_state(project_id, kind))


def state_to_dict(state: PreflightState) -> dict[str, Any]:
    open_b = sum(1 for f in state.findings if f.severity == "blocking" and f.status == "open")
    open_m = sum(1 for f in state.findings if f.severity == "major" and f.status == "open")
    return {
        "project_id": state.project_id,
        "kind": state.kind,
        "content_hash": state.content_hash,
        "findings": [asdict(f) for f in state.findings],
        "events": state.events,
        "finalization": state.finalization,
        "updated_at": state.updated_at,
        "open_blocking": open_b,
        "open_major": open_m,
        "ready": open_b == 0 and bool(state.finalization),
    }


def annotate_storm_meta(markdown: str, *, total_anchors: int | None = None) -> dict[str, Any]:
    """Lightweight check for persist meta (does not block write)."""
    try:
        from ..config import get_settings

        findings = run_checks(
            markdown, total_anchors=total_anchors, settings=get_settings()
        )
    except Exception:  # noqa: BLE001
        findings = run_checks(markdown, total_anchors=total_anchors)
    return {
        "preflight": {
            "content_hash": content_hash(markdown),
            "open_blocking": sum(
                1 for f in findings if f.severity == "blocking" and f.status == "open"
            ),
            "open_major": sum(
                1 for f in findings if f.severity == "major" and f.status == "open"
            ),
            "finding_count": len(findings),
        }
    }

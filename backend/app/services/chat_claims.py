"""Claim-level sourcing for chat answers (+ Wave D sources_audit)."""
from __future__ import annotations

import logging
import re
from typing import Any

from ..config import Settings, get_settings
from ..domain.chat_schemas import SourcedClaim, StructuredAnswer
from ..domain.schemas import Evidence
from ..pipeline.claim_checker import (
    ClaimVerdict,
    VerifiedClaim,
    verify_claim_offline,
    verify_claims_llm,
)

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？.!?])\s*")

# 2026-09-04 (P3): 每次问答新建 executor + shutdown(wait=False) 会让超时的
# deepseek 阻塞线程成为孤儿(60s idle×2 重试 ≈ 2 分钟才自然消亡), 慢窗口
# 高频问答下 OS 线程持续积累。改为模块级共享池(max_workers=2): 超时任务
# 仍占 worker 直至上游返回, 但总数封顶、排队自然节流, 不再无限新增线程。
import concurrent.futures as _cf

_CLAIM_EXECUTOR: _cf.ThreadPoolExecutor | None = None


def _claim_executor() -> _cf.ThreadPoolExecutor:
    global _CLAIM_EXECUTOR
    if _CLAIM_EXECUTOR is None:
        _CLAIM_EXECUTOR = _cf.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="claim-verify"
        )
    return _CLAIM_EXECUTOR


def verify_answer_claims(
    question: str,
    answer: str,
    sources: list[Evidence],
    structured: StructuredAnswer | None = None,
    *,
    settings: Settings | None = None,
) -> list[VerifiedClaim] | None:
    """Run claim verification once; None when claim-check disabled."""
    settings = settings or get_settings()
    if not settings.chat_claim_check_enabled:
        return None

    claims = _extract_claims(answer, structured)
    if not claims:
        return []

    try:
        _ex = _claim_executor()
        _fut = None
        try:
            _fut = _ex.submit(verify_claims_llm, question, claims, sources)
            return list(_fut.result(timeout=12))
        except Exception:
            if _fut is not None:
                _fut.cancel()
            raise
    except Exception:
        return [verify_claim_offline(c, sources) for c in claims]


def build_sourced_claims(
    question: str,
    answer: str,
    sources: list[Evidence],
    structured: StructuredAnswer | None = None,
    *,
    settings: Settings | None = None,
    verified: list[VerifiedClaim] | None = None,
) -> list[SourcedClaim] | None:
    settings = settings or get_settings()
    if verified is None:
        verified = verify_answer_claims(
            question, answer, sources, structured=structured, settings=settings
        )
    if verified is None:
        return None

    out: list[SourcedClaim] = []
    for v in verified:
        chunk_ids = _indices_to_chunk_ids(v.evidence_indices, sources)
        status = _map_verdict(v.verdict)
        conf = 0.9 if status == "supported" else 0.4 if status == "weak" else 0.1
        out.append(
            SourcedClaim(
                text=v.text,
                chunk_ids=chunk_ids,
                confidence=conf,
                status=status,
                raw_verdict=str(getattr(v.verdict, "value", v.verdict))
                if v.verdict is not None
                else None,
            )
        )
    return out


def build_sources_audit(
    sources: list[Evidence],
    *,
    verified: list[VerifiedClaim] | None = None,
    sourced_claims: list[SourcedClaim] | None = None,
    enabled: bool = True,
) -> dict[str, Any] | None:
    """Wave D — claim→passage audit table (fail-open when disabled)."""
    if not enabled:
        return None

    rows: list[dict[str, Any]] = []
    if verified is not None:
        for v in verified:
            chunk_ids = _indices_to_chunk_ids(v.evidence_indices, sources)
            grade = _audit_grade_from_verdict(v.verdict)
            rows.append(
                {
                    "claim": v.text,
                    "grade": grade,
                    "chunk_ids": chunk_ids,
                    "locators": _locators_for_chunks(chunk_ids, sources),
                    "note": (v.reason or "").strip()[:240] or None,
                }
            )
    elif sourced_claims is not None:
        for sc in sourced_claims:
            grade = _audit_grade_from_status(sc.status)
            rows.append(
                {
                    "claim": sc.text,
                    "grade": grade,
                    "chunk_ids": list(sc.chunk_ids or []),
                    "locators": _locators_for_chunks(list(sc.chunk_ids or []), sources),
                    "note": None,
                }
            )
    else:
        return {
            "schema_version": 1,
            "rows": [],
            "summary": {
                "supported": 0,
                "partial": 0,
                "unsupported": 0,
                "contradicted": 0,
            },
        }

    summary = {"supported": 0, "partial": 0, "unsupported": 0, "contradicted": 0}
    for row in rows:
        g = str(row.get("grade") or "unsupported")
        if g in summary:
            summary[g] += 1
        else:
            summary["unsupported"] += 1

    return {"schema_version": 1, "rows": rows, "summary": summary}


def _extract_claims(answer: str, structured: StructuredAnswer | None) -> list[str]:
    if structured and structured.key_findings:
        return [c.strip() for c in structured.key_findings if c.strip()]
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(answer or "") if p.strip()]
    return parts[:8]


def _indices_to_chunk_ids(indices: list[int], sources: list[Evidence]) -> list[str]:
    ids: list[str] = []
    for idx in indices:
        if 0 <= idx < len(sources):
            ident = sources[idx].identifier or ""
            if ident.startswith("kb:"):
                ids.append(ident[3:])
            elif ident:
                ids.append(ident)
    return list(dict.fromkeys(ids))


def _map_verdict(verdict: ClaimVerdict) -> str:
    """Legacy SourcedClaim status (conflicting stays weak for back-compat)."""
    if verdict == ClaimVerdict.supported:
        return "supported"
    if verdict in (ClaimVerdict.insufficient, ClaimVerdict.conflicting):
        return "weak"
    return "unsupported"


def _audit_grade_from_verdict(verdict: ClaimVerdict) -> str:
    if verdict == ClaimVerdict.supported:
        return "supported"
    if verdict == ClaimVerdict.insufficient:
        return "partial"
    if verdict == ClaimVerdict.conflicting:
        return "contradicted"
    return "unsupported"


def _audit_grade_from_status(status: str) -> str:
    if status == "supported":
        return "supported"
    if status == "weak":
        return "partial"
    return "unsupported"


def _locators_for_chunks(
    chunk_ids: list[str], sources: list[Evidence]
) -> list[dict[str, Any]]:
    by_id: dict[str, Evidence] = {}
    for ev in sources:
        ident = ev.identifier or ""
        if ident.startswith("kb:"):
            by_id[ident[3:]] = ev
        if ident:
            by_id[ident] = ev
    out: list[dict[str, Any]] = []
    for cid in chunk_ids:
        ev = by_id.get(cid)
        if ev is None:
            out.append({"chunk_id": cid, "page": None, "paragraph": None})
            continue
        out.append(
            {
                "chunk_id": cid,
                "page": ev.page,
                "paragraph": ev.paragraph,
            }
        )
    return out

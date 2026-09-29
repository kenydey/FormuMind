"""Wave 3-2: lightweight operational stats.

In-process counters only — no new storage. Counters reset on process
restart; the response says so explicitly. For durable history use the
structured logs (``tier2 evidence compression: ...``).
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/ops", tags=["ops"])


@router.get("/evidence-stats")
def evidence_stats() -> dict:
    """Tier-2 (LLM) evidence compression counters for this process.

    Counters are cumulative since process start and reset on restart.
    """
    from ..services.query_aware_compression import get_evidence_stats

    stats = get_evidence_stats()
    attempts = stats.get("tier2_attempts", 0)
    triggers = stats.get("tier2_triggers", 0)
    tokens_before = stats.get("tokens_before", 0)
    tokens_after = stats.get("tokens_after", 0)
    # P3-5: ingest-time embedding coverage (process-local, reset on restart).
    kb_cov = {}
    kb_coverage_rate = None
    try:
        from ..services.kb_index import get_kb_coverage_stats

        kb_cov = get_kb_coverage_stats()
        total = kb_cov.get("kb_chunks_total", 0)
        kb_coverage_rate = (kb_cov.get("kb_chunks_embedded", 0) / total) if total else None
    except Exception:
        pass
    return {
        **stats,
        **kb_cov,
        "kb_embedding_coverage": kb_coverage_rate,
        "tokens_saved": tokens_before - tokens_after,
        "trigger_rate": (triggers / attempts) if attempts else 0.0,
        "process_local": True,
        "note": "counters reset on process restart; see logs for per-query lines",
    }

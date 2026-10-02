"""Wave 3-2: lightweight operational stats.

In-process counters only — no new storage. Counters reset on process
restart; the response says so explicitly. For durable history use the
structured logs (``tier2 evidence compression: ...``).
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/ops", tags=["ops"])


@router.get("/kb-health")
def kb_health() -> dict:
    """B-9: KB 健康仪表盘 v1。

    parser 分布、embedding 覆盖率（chunk 表实测）、空文档率（零切块文档占比）。
    读失败时返回 ``{"available": False}``（fail-open）。
    """
    from ..services.kb_index import kb_health_snapshot

    return kb_health_snapshot()


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


@router.get("/recommend-stats")
def recommend_stats(
    project_id: str | None = None,
    days: int = 30,
) -> dict:
    """C-8: recommendation adoption telemetry (weak-success layer).

    Totals, adoption rate, and per-signal distribution over the trailing
    ``days`` window. Fail-open: returns empty stats (``available: False``)
    when the ``recommend_outcomes`` table does not exist yet.
    ``validated`` counts rounds with ``experiment_validated=True`` (U-4:
    sync-datalab 成功后按 snapshot 指纹 fail-open 回写；C-5 pending 时恒为
    null 的旧契约已过时）。
    """
    from ..db import recommend_outcome_store
    from ..db.database import default_session_factory

    days = max(1, min(int(days), 365))
    factory = default_session_factory()
    with factory() as session:
        return recommend_outcome_store.outcome_stats(
            session, project_id=project_id or None, days=days
        )

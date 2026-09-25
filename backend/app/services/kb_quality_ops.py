"""KB quality ops aggregation for Hub panel (Batch D).

Read-only: composes existing /stats, relevance-shadow stats, and ingest audit
signals into a single payload + heuristic ``kb_quality_score`` (0–100).
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _clamp(n: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, n))


def compute_kb_quality_score(
    *,
    sources_active: int,
    sources_archived: int,
    embedded_chunks: int,
    chunks_active: int,
    scan_pressure: float,
    topicality_reject_pct: float | None,
    fulltext_fail_pct: float | None,
    vector_mode: str,
) -> dict[str, Any]:
    """Heuristic 0–100 score with component breakdown (display only)."""
    components: dict[str, float] = {}

    # Embedding coverage among active chunks
    if chunks_active > 0:
        embed_ratio = embedded_chunks / max(chunks_active, 1)
        components["embed_coverage"] = round(embed_ratio * 30.0, 2)
    else:
        components["embed_coverage"] = 10.0 if sources_active == 0 else 0.0

    # Archive hygiene: some archive OK; >50% archived hurts
    total_src = sources_active + sources_archived
    if total_src <= 0:
        components["archive_hygiene"] = 15.0
    else:
        archived_ratio = sources_archived / total_src
        components["archive_hygiene"] = round(_clamp(20.0 * (1.0 - archived_ratio)), 2)

    # Scan headroom
    pressure = float(scan_pressure or 0.0)
    components["scan_headroom"] = round(_clamp(20.0 * (1.0 - pressure)), 2)

    # Topicality gate health (lower reject under enforce ≈ healthier corpus for query)
    if topicality_reject_pct is None:
        components["topicality"] = 10.0
    else:
        # 0% reject → full; 50%+ → 0
        components["topicality"] = round(_clamp(15.0 * (1.0 - float(topicality_reject_pct) / 50.0)), 2)

    # Fulltext success
    if fulltext_fail_pct is None:
        components["fulltext"] = 10.0
    else:
        components["fulltext"] = round(_clamp(15.0 * (1.0 - float(fulltext_fail_pct) / 50.0)), 2)

    # Vector mode bonus/penalty
    mode = (vector_mode or "empty").lower()
    if mode == "semantic":
        components["vector_mode"] = 10.0
    elif mode == "degraded":
        components["vector_mode"] = 2.0
    elif mode == "keyword":
        components["vector_mode"] = 5.0
    else:
        components["vector_mode"] = 3.0

    score = round(_clamp(sum(components.values())), 1)
    return {"score": score, "components": components}


def _topicality_reject_pct(shadow: dict[str, Any] | None) -> float | None:
    if not shadow:
        return None
    if shadow.get("would_reject_pct") is not None and int(shadow.get("samples") or 0) > 0:
        return float(shadow["would_reject_pct"])
    recent = shadow.get("recent") or []
    if not isinstance(recent, list) or not recent:
        return None
    ns = 0
    rejs = 0
    for b in recent:
        if not isinstance(b, dict):
            continue
        n = int(b.get("n") or 0)
        ns += n
        rejs += int(b.get("would_reject") or 0)
    if ns <= 0:
        return None
    return round(100.0 * rejs / ns, 2)


def _fulltext_fail_pct_from_gate(gate_drops: dict[str, Any] | None) -> float | None:
    """Approximate failure pressure from ingest gate drops (no separate fulltext JSONL)."""
    if not isinstance(gate_drops, dict):
        return None
    ingest = gate_drops.get("ingest") or {}
    if not isinstance(ingest, dict) or not ingest:
        return None
    total = sum(int(v or 0) for v in ingest.values())
    # No absolute ingest attempts here — return None rather than invent a rate.
    if total <= 0:
        return None
    return None


def build_quality_ops(*, project_id: str | None = None) -> dict[str, Any]:
    """Compose Hub quality-ops payload."""
    from . import kb_index
    from .kb_ingest_audit import load_relevance_shadow_stats

    stats = kb_index.kb_stats() if hasattr(kb_index, "kb_stats") else {}
    if not isinstance(stats, dict):
        stats = {}

    shadow = load_relevance_shadow_stats(limit=50)
    if not isinstance(shadow, dict):
        shadow = {}
    topicality_pct = _topicality_reject_pct(shadow)
    gate_drops = stats.get("quality_gate_drops") or {}
    fulltext_pct = _fulltext_fail_pct_from_gate(gate_drops if isinstance(gate_drops, dict) else None)

    sources_active = int(stats.get("sources_active") or 0)
    sources_archived = int(stats.get("sources_archived") or 0)
    chunks_active = int(stats.get("chunks_active") or stats.get("chunks") or 0)
    embedded = int(stats.get("embedded_chunks") or 0)
    scan_pressure = float(stats.get("scan_pressure") or 0.0)
    vector_mode = str(stats.get("vector_mode") or "empty")

    quality = compute_kb_quality_score(
        sources_active=sources_active,
        sources_archived=sources_archived,
        embedded_chunks=embedded,
        chunks_active=chunks_active,
        scan_pressure=scan_pressure,
        topicality_reject_pct=topicality_pct,
        fulltext_fail_pct=fulltext_pct,
        vector_mode=vector_mode,
    )

    hybrid_lat: dict[str, Any] = {}
    try:
        from .hybrid_search import hybrid_latency_stats

        hybrid_lat = hybrid_latency_stats()
    except Exception as exc:
        logger.debug("hybrid latency stats unavailable: %s", exc)
        hybrid_lat = {"n": 0, "p50_ms": None, "p95_ms": None, "ann_last": False}

    return {
        "project_id": (project_id or "").strip() or None,
        "kb_quality_score": quality["score"],
        "kb_quality_components": quality["components"],
        "sources_active": sources_active,
        "sources_archived": sources_archived,
        "chunks_active": chunks_active,
        "chunks_archived": int(stats.get("chunks_archived") or 0),
        "embedded_chunks": embedded,
        "scan_limit": int(stats.get("scan_limit") or 5000),
        "scan_pressure": scan_pressure,
        "scan_near_cap": bool(stats.get("scan_near_cap")),
        "vector_mode": vector_mode,
        "vector_hint": stats.get("vector_hint") or "",
        "topicality_would_reject_pct": topicality_pct,
        "fulltext_fail_pct": fulltext_pct,
        "quality_gate_drops": gate_drops,
        "hybrid_search_latency": hybrid_lat,
        "relevance_shadow": {
            "batch_count": int(shadow.get("batches") or 0),
            "samples": int(shadow.get("samples") or 0),
            "would_reject": int(shadow.get("would_reject") or 0),
            "would_reject_pct": shadow.get("would_reject_pct"),
            "shadow_only_reject": shadow.get("shadow_only_reject"),
            "topic_only_reject": shadow.get("topic_only_reject"),
            "both_reject": shadow.get("both_reject"),
        },
        "notes": [
            "kb_quality_score 为只读启发式，不改变入库/检索行为。",
            "topicality 真闸默认开启（kb_relevance_shadow=False）；shadow stats 仅供回滚校准。",
            "retention purge 默认 dry-run，不会自动物理删除。",
            "hybrid_search_latency 来自持久化 KB hybrid（≠ 会话 rag.BM25FAISSStore）；"
            "scan_near_cap 或 p95 超阈时启用 BM25 预筛 + 子集 cosine（不引 Qdrant）。",
        ],
    }

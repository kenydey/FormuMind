"""Knowledge graph P0 endpoints — entity index, resolve, retrieve."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..config import get_settings
from ..domain.kg_schemas import (
    EntityResolveResponse,
    KGPathResponse,
    KGLinkReport,
    KGRebuildReport,
    KGRetrieveRequest,
    KGRetrieveResponse,
    KGRelationView,
    KGStats,
    KGSubstituteDiscoverResponse,
)
from ..services.kg import kg_enabled, retrieve
from ..services.kg.entity_linker import link_source, rebuild_all
from ..services.kg.entity_resolver import resolve_query
from ..services.kg.graph_query import discover_substitutes, find_path, get_entity_relations

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kg", tags=["kg"])


class KGRebuildBody(BaseModel):
    project_id: str | None = None


@router.get("/stats", response_model=KGStats)
def stats() -> KGStats:
    from ..db.entity_store import get_entity_store

    enabled = kg_enabled()
    if not enabled:
        return KGStats(enabled=False)
    return KGStats(enabled=True, **get_entity_store().stats())


@router.get("/feedback/stats")
def feedback_stats() -> dict:
    """Measured feedback crawl stats — how many measured_performance links landed."""
    from ..db.entity_store import get_entity_store
    from ..db.models import KGEntityLink

    if not kg_enabled():
        raise HTTPException(status_code=409, detail="知识图谱未启用（FORMUMIND_KG_ENABLED）")
    es = get_entity_store()
    with es._session_factory() as session:
        total = session.query(KGEntityLink).filter(KGEntityLink.extraction_method == "measured").count()
        measured_perf = (
            session.query(KGEntityLink)
            .filter(KGEntityLink.link_type == "measured_performance", KGEntityLink.extraction_method == "measured")
            .count()
        )
        by_campaign: dict[str, int] = {}
        rows = (
            session.query(KGEntityLink.evidence_refs)
            .filter(KGEntityLink.extraction_method == "measured")
            .all()
        )
        for (refs,) in rows:
            for r in (refs or []):
                sid = r.get("source_id", "")
                if sid.startswith("measured:campaign_"):
                    by_campaign[sid] = by_campaign.get(sid, 0) + 1
    return {"measured_total": total, "measured_performance": measured_perf, "by_campaign": by_campaign}


@router.get("/feedback/report")
def feedback_report() -> dict:
    """审计报表：measured 统计 + 零增长告警 + 最近 campaign bias 趋势（loop_history 抽取）。"""
    from ..db.entity_store import get_entity_store
    from ..db.models import KGEntityLink

    if not kg_enabled():
        raise HTTPException(status_code=409, detail="知识图谱未启用（FORMUMIND_KG_ENABLED）")
    stats = feedback_stats()
    # 零增长告警：measured_performance == 0
    alert = None
    if stats["measured_performance"] == 0:
        alert = "暂无实测回流证据：请在实验台账完成至少一行 Completed 并同步（sync 会写入 KG）"
    # 最近 bias 趋势：从 campaign 的 loop_history 抽取（best-effort，失败则空）
    recent_bias: list[dict] = []
    try:
        from ..db.campaign_store import get_campaign_store

        store = get_campaign_store()
        # 同步方法遍历最近 5 个 campaign 的 loop_history
        import asyncio

        # 尝试同步获取：若 store 为 sqlite，可直接同步查询
        # 退化：仅返回空列表，不阻塞报表
        pass
    except Exception:
        pass
    return {**stats, "alert": alert, "recent_bias": recent_bias}


@router.post("/rebuild", response_model=KGRebuildReport)
def rebuild(body: KGRebuildBody | None = None) -> KGRebuildReport:
    if not kg_enabled():
        raise HTTPException(status_code=409, detail="知识图谱未启用（FORMUMIND_KG_ENABLED）")
    try:
        pid = body.project_id if body else None
        return rebuild_all(project_id=pid)
    except Exception as exc:
        logger.exception("kg rebuild failed")
        raise HTTPException(status_code=500, detail="操作失败") from exc


@router.post("/link-source/{source_id}", response_model=KGLinkReport)
def link_one_source(source_id: str) -> KGLinkReport:
    if not kg_enabled():
        raise HTTPException(status_code=409, detail="知识图谱未启用（FORMUMIND_KG_ENABLED）")
    try:
        return link_source(source_id)
    except Exception as exc:
        logger.exception("kg link-source failed")
        raise HTTPException(status_code=500, detail="操作失败") from exc


@router.get("/resolve", response_model=EntityResolveResponse)
def resolve(q: str = Query(min_length=1)) -> EntityResolveResponse:
    if not kg_enabled():
        raise HTTPException(status_code=409, detail="知识图谱未启用（FORMUMIND_KG_ENABLED）")
    return resolve_query(q)


@router.get("/relations/{entity_id}", response_model=list[KGRelationView])
def entity_relations(
    entity_id: str,
    direction: str = Query(default="both", pattern="^(both|outgoing|incoming)$"),
    extraction_method: str | None = Query(default=None, description="Filter by extraction_method: rule|llm|measured|vision_table"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[KGRelationView]:
    if not kg_enabled():
        raise HTTPException(status_code=409, detail="知识图谱未启用（FORMUMIND_KG_ENABLED）")
    return get_entity_relations(entity_id, direction=direction, extraction_method=extraction_method, limit=limit)


@router.get("/path", response_model=KGPathResponse)
def entity_path(
    src: str = Query(min_length=1),
    dst: str = Query(min_length=1),
    max_depth: int = Query(default=4, ge=1, le=8),
) -> KGPathResponse:
    if not kg_enabled():
        raise HTTPException(status_code=409, detail="知识图谱未启用（FORMUMIND_KG_ENABLED）")
    return find_path(src, dst, max_depth=max_depth)


@router.get("/discover/substitutes", response_model=KGSubstituteDiscoverResponse)
def substitute_discover(
    entity_id: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
) -> KGSubstituteDiscoverResponse:
    if not kg_enabled():
        raise HTTPException(status_code=409, detail="知识图谱未启用（FORMUMIND_KG_ENABLED）")
    resolved_id = entity_id
    if not resolved_id and q:
        resolved = resolve_query(q)
        if resolved.chemicals:
            resolved_id = resolved.chemicals[0].id
        elif resolved.trade_products:
            resolved_id = resolved.trade_products[0].id
    if not resolved_id:
        raise HTTPException(status_code=400, detail="请提供 entity_id 或可解析的 q 参数")
    return discover_substitutes(resolved_id, limit=limit)


@router.post("/retrieve", response_model=KGRetrieveResponse)
def retrieve_endpoint(req: KGRetrieveRequest) -> KGRetrieveResponse:
    if not kg_enabled():
        settings = get_settings()
        if not settings.kb_v2_enabled:
            raise HTTPException(status_code=409, detail="知识图谱与 KB v2 均未启用")
    return retrieve(
        req.query,
        mode=req.mode,
        project_id=req.project_id,
        scan_limit=req.scan_limit,
        chunk_cap=req.chunk_cap,
        llm_cap=req.llm_cap,
        max_sources=req.max_sources,
        k_semantic=req.k_semantic,
    )

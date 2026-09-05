"""Knowledge graph P0 endpoints — entity index, resolve, retrieve."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..config import get_settings
from ..domain.kg_schemas import (
    EntityResolveResponse,
    KGContradictionResponse,
    KGPathResponse,
    KGLinkReport,
    KGRebuildReport,
    KGRetrieveRequest,
    KGRetrieveResponse,
    KGRelationView,
    SimilarFormulationRequest,
    SimilarFormulationResponse,
    KGStats,
    KGSubstituteDiscoverResponse,
)
from ..services.kg import kg_enabled, retrieve
from ..services.kg.contradiction import (
    detect_contradictions,
    detect_contradictions_by_query,
)
from ..services.kg.entity_linker import link_source, rebuild_all
from ..services.kg.entity_resolver import resolve_query
from ..services.kg.graph_query import discover_substitutes, find_path, get_entity_relations
from ..services.kg.formulation_similarity import find_similar_formulations

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kg", tags=["kg"])


class KGRebuildBody(BaseModel):
    project_id: str | None = None


@router.get("/stats", response_model=KGStats)
def stats() -> KGStats:
    """KG corpus counters + relation-layer health (B10).

    ``relation_layer_empty`` is true when entities exist but ``kb_entity_links``
    is still 0 — the failure mode that left production with 715 entities /
    70k mentions and zero usable relations. Surface it so Settings can warn
    and operators know to run ``POST /api/kg/relations/rebuild``.
    """
    from ..db.entity_store import get_entity_store

    enabled = kg_enabled()
    if not enabled:
        return KGStats(enabled=False)
    raw = get_entity_store().stats()
    relation_layer_empty = bool(raw.get("entities", 0) > 0 and raw.get("links", 0) == 0)
    warnings: list[str] = []
    if relation_layer_empty:
        warnings.append(
            "关系层为空：已有实体但 kb_entity_links=0。"
            "请开启「入库关系提取」或执行「补语义关系」"
            "（POST /api/kg/relations/rebuild）。"
        )
    return KGStats(
        enabled=True,
        relation_layer_empty=relation_layer_empty,
        warnings=warnings,
        **raw,
    )


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
    from ..db.campaign_store import get_campaign_store
    from ..db.models import KGEntityLink

    if not kg_enabled():
        raise HTTPException(status_code=409, detail="知识图谱未启用（FORMUMIND_KG_ENABLED）")
    stats = feedback_stats()
    # 零增长告警：measured_performance == 0
    alert = None
    if stats["measured_performance"] == 0:
        alert = "暂无实测回流证据：请在实验台账完成至少一行 Completed 并同步（sync 会写入 KG）"
    # 最近 bias 趋势：从各 campaign 的 loop_history 末条 entry 抽 rmse_by_metric
    recent_bias: list[dict] = []
    try:
        from ..db.campaign_store import get_campaign_store
        from ..db.models import Campaign as CampaignModel

        store = get_campaign_store()
        with store._session_factory() as session:
            campaigns = session.query(CampaignModel).order_by(CampaignModel.id.desc()).limit(20).all()
        for c in campaigns:
            history = list(getattr(c, "loop_history", None) or [])
            if not history:
                continue
            last = history[-1]
            rmse_map = last.get("rmse_by_metric") or {}
            if not rmse_map:
                continue
            trend = "improving" if (last.get("converged") or float(min(rmse_map.values())) < 0.2) else "unsettled"
            recent_bias.append({
                "campaign_id": getattr(c, "id", None),
                "campaign_name": getattr(c, "name", ""),
                "primary_metric": getattr(c, "primary_metric", None),
                "rmse_by_metric": rmse_map,
                "trend": trend,
                "converged": bool(last.get("converged")),
            })
    except Exception as exc:  # best-effort：任何异常都不应让报表 500
        logger.warning("feedback_report recent_bias extract failed: %s", exc)
        recent_bias = []
    return {**stats, "alert": alert, "recent_bias": recent_bias}


@router.get("/calibration", include_in_schema=False)
def calibration() -> dict:
    """KG 权重校准：返回当前 penalty/bonus 与命中计数，供调参。"""
    s = get_settings()
    from ..db.entity_store import get_entity_store
    from ..db.models import KGEntityLink

    inh = sub = syn = 0
    if kg_enabled():
        es = get_entity_store()
        with es._session_factory() as session:
            inh = session.query(KGEntityLink).filter(KGEntityLink.link_type == "inhibits").count()
            sub = session.query(KGEntityLink).filter(KGEntityLink.link_type == "substitutes").count()
            syn = session.query(KGEntityLink).filter(KGEntityLink.link_type == "synergizes").count()
    return {
        "kg_enabled": bool(s.kg_enabled),
        "kg_inhibits_penalty": float(getattr(s, "kg_inhibits_penalty", 0.5)),
        "kg_synergizes_bonus": float(getattr(s, "kg_synergizes_bonus", 1.0)),
        "kg_measured_bonus": float(getattr(s, "kg_measured_bonus", 1.15)),
        "counts": {"inhibits": inh, "substitutes": sub, "synergizes": syn},
    }


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


class KgRelationsRebuildBody(BaseModel):
    """关系重建请求: source_id 限定单源(None=全部含提及的源, LLM 慢)."""

    source_id: str | None = None


@router.post("/relations/rebuild")
def rebuild_relations_api(body: KgRelationsRebuildBody | None = None):
    """异步补语义关系(2026-09-05): 实体/提及已在库, 只重跑关系提取。

    关系层(LLM)可能数十秒~数十分钟, 走 celery 异步 + 202 accepted,
    前端轮询 GET /api/tasks/{task_id}。同步 CLI 等价:
    python -m app.services.kg.rebuild_relations --all
    """
    if not kg_enabled():
        raise HTTPException(status_code=409, detail="知识图谱未启用（FORMUMIND_KG_ENABLED）")
    from ..worker.tasks import run_kg_relations_rebuild
    from ._dispatch import submit

    payload = {"source_id": (body.source_id if body else None)}
    return submit(run_kg_relations_rebuild, payload, "kg_relations_rebuild")


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


@router.get("/path", response_model=KGPathResponse, include_in_schema=False)
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


@router.get("/contradictions", response_model=KGContradictionResponse)
def contradictions(
    entity_id: str | None = Query(default=None),
    q: str | None = Query(default=None),
) -> KGContradictionResponse:
    """KG v10 — 文献关系 vs 团队实测的矛盾检测（只读视图）。

    对 ``entity_id``（或 ``q`` 解析出的实体）比较其文献语义关系
    （substitutes/synergizes/inhibits/...）与 ``kg_feedback`` 写回的
    ``measured_performance`` 实测边，标记方向冲突。无实测实体返回空列表。
    """
    if not kg_enabled():
        raise HTTPException(status_code=409, detail="知识图谱未启用（FORMUMIND_KG_ENABLED）")
    if entity_id:
        return detect_contradictions(entity_id)
    if q:
        return detect_contradictions_by_query(q)
    raise HTTPException(status_code=400, detail="请提供 entity_id 或可解析的 q 参数")


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

@router.post("/formulations/similar", response_model=SimilarFormulationResponse)
def similar_formulations(req: SimilarFormulationRequest) -> SimilarFormulationResponse:
    """Find historically similar formulations across projects."""
    from ..db.database import default_session_factory
    from ..db.models import ExperimentRow, ProjectRow

    factory = default_session_factory()
    with factory() as session:
        q = session.query(ExperimentRow)
        if req.domain:
            q = q.filter(ExperimentRow.domain == req.domain)
        rows = q.all()
        all_exps = [
            {
                "id": r.id,
                "project_id": r.project_id or "",
                "domain": r.domain or "",
                "factors": r.factors or {},
                "measured": r.measured or {},
            }
            for r in rows
        ]

    matches_raw = find_similar_formulations(
        req.factors,
        all_exps,
        domain=req.domain,
        exclude_project_id=req.exclude_project_id,
        min_similarity=req.min_similarity,
        limit=req.limit,
    )

    # Enrich with project titles
    project_ids = {m["project_id"] for m in matches_raw if m.get("project_id")}
    project_titles = {}
    if project_ids:
        with factory() as session:
            for pid in project_ids:
                proj = session.query(ProjectRow).filter(ProjectRow.id == pid).first()
                if proj:
                    project_titles[pid] = proj.title

    matches = []
    for m in matches_raw:
        factors = m.get("factors", {})
        query_ings = set(req.factors.keys())
        match_ings = set(factors.keys())
        shared = list(query_ings & match_ings)
        differing = list(match_ings - query_ings)
        matches.append(SimilarFormulationMatch(
            experiment_id=m["experiment_id"],
            project_id=m.get("project_id", ""),
            project_title=project_titles.get(m.get("project_id", "")),
            similarity=m["similarity"],
            factors=factors,
            measured={k: v for k, v in (m.get("measured") or {}).items() if v is not None},
            shared_ingredients=shared,
            differing_ingredients=differing,
        ))

    return SimilarFormulationResponse(matches=matches, query_factors=req.factors)


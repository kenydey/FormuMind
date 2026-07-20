"""Knowledge graph P0 endpoints — entity index, resolve, retrieve."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..config import get_settings
from ..domain.kg_schemas import (
    EntityResolveResponse,
    KGLinkReport,
    KGRebuildReport,
    KGRetrieveRequest,
    KGRetrieveResponse,
    KGStats,
)
from ..services.kg import kg_enabled, retrieve
from ..services.kg.entity_linker import link_source, rebuild_all
from ..services.kg.entity_resolver import resolve_query

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


@router.post("/rebuild", response_model=KGRebuildReport)
def rebuild(body: KGRebuildBody | None = None) -> KGRebuildReport:
    if not kg_enabled():
        raise HTTPException(status_code=409, detail="知识图谱未启用（FORMUMIND_KG_ENABLED）")
    try:
        pid = body.project_id if body else None
        return rebuild_all(project_id=pid)
    except Exception as exc:
        logger.exception("kg rebuild failed")
        raise HTTPException(status_code=500, detail=f"重建知识图谱失败：{exc}") from exc


@router.post("/link-source/{source_id}", response_model=KGLinkReport)
def link_one_source(source_id: str) -> KGLinkReport:
    if not kg_enabled():
        raise HTTPException(status_code=409, detail="知识图谱未启用（FORMUMIND_KG_ENABLED）")
    try:
        return link_source(source_id)
    except Exception as exc:
        logger.exception("kg link-source failed")
        raise HTTPException(status_code=500, detail=f"实体链接失败：{exc}") from exc


@router.get("/resolve", response_model=EntityResolveResponse)
def resolve(q: str = Query(min_length=1)) -> EntityResolveResponse:
    if not kg_enabled():
        raise HTTPException(status_code=409, detail="知识图谱未启用（FORMUMIND_KG_ENABLED）")
    return resolve_query(q)


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

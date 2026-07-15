"""Persistent knowledge-base endpoints (KB v2).

GET  /api/kb/stats    — corpus counters (sources, chunks, embeddings)
POST /api/kb/reindex  — rebuild chunk rows for every stored source
GET  /api/kb/search   — direct chunk retrieval (debug / power users)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..domain.schemas import Evidence
from ..services import kb_index

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kb", tags=["kb"])


class KBStats(BaseModel):
    enabled: bool
    sources: int
    sources_by_kind: dict[str, int]
    chunks: int
    embedded_chunks: int
    embedding_available: bool
    products: int = 0


class ReindexResult(BaseModel):
    reindexed_sources: int
    reindexed_chunks: int
    total_chunks: int
    embedded_chunks: int


class KBSearchResponse(BaseModel):
    results: list[Evidence]


@router.get("/stats", response_model=KBStats)
def stats() -> KBStats:
    return KBStats(**kb_index.kb_stats())


@router.post("/reindex", response_model=ReindexResult)
def reindex(embed: bool = True) -> ReindexResult:
    if not kb_index.kb_enabled():
        raise HTTPException(status_code=409, detail="知识库 v2 未启用（FORMUMIND_KB_V2_ENABLED）")
    try:
        return ReindexResult(**kb_index.reindex_all(embed=embed))
    except Exception as exc:
        logger.exception("kb reindex failed")
        raise HTTPException(status_code=500, detail=f"重建知识库失败：{exc}") from exc


@router.get("/search", response_model=KBSearchResponse)
def search(
    q: str = Query(min_length=1),
    k: int = Query(default=6, ge=1, le=50),
    project_id: str | None = Query(default=None),
) -> KBSearchResponse:
    return KBSearchResponse(results=kb_index.search_chunks(q, k=k, project_id=project_id))


class KBSourceItem(BaseModel):
    id: str
    title: str
    filename: str
    source_kind: str
    origin_url: str | None = None
    project_id: str | None = None
    raw_text_chars: int = 0
    extraction_status: str = ""


class KBSourcesResponse(BaseModel):
    sources: list[KBSourceItem]
    total: int


@router.get("/sources", response_model=KBSourcesResponse)
def list_sources(
    project_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> KBSourcesResponse:
    from ..db.source_store import get_source_store

    rows = get_source_store().list_for_project(project_id, limit=limit)
    return KBSourcesResponse(
        sources=[
            KBSourceItem(
                id=r.id,
                title=r.title or r.filename,
                filename=r.filename,
                source_kind=r.source_kind,
                origin_url=r.origin_url,
                project_id=r.project_id,
                raw_text_chars=int(r.raw_text_chars or 0),
                extraction_status=r.extraction_status or "",
            )
            for r in rows
        ],
        total=len(rows),
    )


class KBProductItem(BaseModel):
    trade_name: str
    grade: str = ""
    supplier: str = ""
    generic_name: str = ""
    cas: str = ""
    smiles: str | None = None
    role: str = ""
    mention_count: int = 0
    sources: int = 0


class KBProductsResponse(BaseModel):
    products: list[KBProductItem]
    total: int


@router.get("/products", response_model=KBProductsResponse)
def products(
    q: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> KBProductsResponse:
    """Corpus-derived commercial product registry (牌号/供应商/通用名)."""
    from ..db.product_store import get_product_store

    store = get_product_store()
    rows = store.search(q, limit=limit, offset=offset)
    return KBProductsResponse(
        products=[
            KBProductItem(
                trade_name=r.trade_name,
                grade=r.grade or "",
                supplier=r.supplier or "",
                generic_name=r.generic_name or "",
                cas=r.cas or "",
                smiles=r.smiles,
                role=r.role or "",
                mention_count=int(r.mention_count or 0),
                sources=len(r.source_ids or []),
            )
            for r in rows
        ],
        total=store.count(),
    )

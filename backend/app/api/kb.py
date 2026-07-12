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
def search(q: str = Query(min_length=1), k: int = Query(default=6, ge=1, le=50)) -> KBSearchResponse:
    return KBSearchResponse(results=kb_index.search_chunks(q, k=k))

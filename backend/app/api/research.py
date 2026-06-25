"""Research endpoint: CRAG graph via Celery + SSE task stream."""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from ..domain.schemas import Evidence, Requirement, ResearchResult
from ..pipeline import workflow
from ..services.deep_research import ExpandedQuery, QueryExpander
from ..services.federated_search import FederatedSearchEngine
from ..worker.tasks import run_deep_research_task, run_recommend_task
from .tasks import accepted_response

router = APIRouter(prefix="/api", tags=["research"])


class ResearchRequest(Requirement):
    sources: list[Evidence] = Field(default_factory=list)
    source_types: list[str] = Field(default_factory=list, deprecated=True)
    query: str = ""


class DeepResearchRequest(BaseModel):
    topic: str = Field(min_length=1)
    requirement: Requirement
    sources: list[Evidence] = Field(default_factory=list)
    query: str = ""


@router.post("/research", response_model=ResearchResult)
def start_research(body: ResearchRequest) -> ResearchResult:
    """同步配方推荐：CRAG graph → grounded evidence → 推荐。"""
    if body.source_types:
        logger.warning("POST /api/research source_types ignored; use ColBERT KB + CRAG fallback")
    req = Requirement(**{
        k: v for k, v in body.model_dump().items()
        if k not in ("sources", "source_types", "query")
    })
    pre_sources = body.sources if body.sources else None
    return workflow.run_research(req, pre_sources=pre_sources, query=body.query)


@router.post("/research/recommend", status_code=202)
def start_recommend_research(body: ResearchRequest) -> JSONResponse:
    """Enqueue lightweight CRAG recommend; subscribe via GET /api/tasks/{id}/stream."""
    req = Requirement(**{
        k: v for k, v in body.model_dump().items()
        if k not in ("sources", "source_types", "query")
    })
    payload = {
        "topic": body.query or req.headline(),
        "requirement": req.model_dump(),
        "sources": [s.model_dump() for s in body.sources],
        "query": body.query or req.headline(),
    }
    async_result = run_recommend_task.delay(payload)
    return accepted_response(async_result.id, "recommend")


@router.post("/research/deep", status_code=202)
def start_deep_research(body: DeepResearchRequest) -> JSONResponse:
    """Enqueue CRAG deep research; subscribe via GET /api/tasks/{id}/stream."""
    payload = {
        "topic": body.topic,
        "requirement": body.requirement.model_dump(),
        "sources": [s.model_dump() for s in body.sources],
        "query": body.query or body.topic,
    }
    async_result = run_deep_research_task.delay(payload)
    return accepted_response(async_result.id, "deep_research")


@router.post("/research/kb/refresh")
def refresh_knowledge_base(query: str = Query(..., min_length=1)) -> dict:
    from ..services import colbert_store

    fed = FederatedSearchEngine()
    result = fed.search(query)
    indexed = colbert_store.index_evidence(result.evidence) if result.evidence else 0
    return {
        "query": query,
        "fetched": len(result.evidence),
        "indexed_total": indexed,
        "source_counts": result.source_counts,
    }


@router.get("/research/expand", response_model=ExpandedQuery, deprecated=True)
def expand_research_query(topic: str = Query(..., min_length=1)) -> ExpandedQuery:
    return QueryExpander().expand(topic)

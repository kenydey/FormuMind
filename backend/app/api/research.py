"""Research endpoint: CRAG graph via Celery + SSE task stream."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from ..domain.schemas import Evidence, Formulation, Requirement, ResearchResult
from ..pipeline import workflow
from ..services.deep_research import ExpandedQuery, QueryExpander
from ..worker.tasks import run_deep_research_task, run_recommend_task
from ._dispatch import submit
from ._idempotency import enqueue_outbox, idempotency_key

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


class ModifyRequest(BaseModel):
    requirement: Requirement
    modify_prompt: str = Field(min_length=1)
    sources: list[Evidence] = Field(default_factory=list)
    base_formulas: list[Formulation] = Field(default_factory=list)
    base_formulation: Formulation | None = None
    query: str = ""
    n: int = Field(default=3, ge=1, le=8)


# Idempotency helpers live in _idempotency.py — shared with the other
# async-submission endpoints.
_idempotency_key = idempotency_key
_enqueue_outbox = enqueue_outbox


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
    result = workflow.run_research(req, pre_sources=pre_sources, query=body.query)
    return result


@router.post("/research/recommend", status_code=202)
def start_recommend_research(body: ResearchRequest, request: Request) -> JSONResponse:
    """Enqueue lightweight CRAG recommend; subscribe via GET /api/tasks/{id}/stream."""
    from ..middleware.api_auth import get_current_owner

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
    outbox_id = _enqueue_outbox("research_recommend", payload)
    return submit(run_recommend_task, payload, "recommend", outbox_id=outbox_id, owner_id=get_current_owner(request))


@router.post("/research/deep", status_code=202)
def start_deep_research(body: DeepResearchRequest, request: Request) -> JSONResponse:
    """Enqueue CRAG deep research; subscribe via GET /api/tasks/{id}/stream."""
    from ..middleware.api_auth import get_current_owner

    payload = {
        "topic": body.topic,
        "requirement": body.requirement.model_dump(),
        "sources": [s.model_dump() for s in body.sources],
        "query": body.query or body.topic,
    }
    outbox_id = _enqueue_outbox("research_deep", payload)
    return submit(run_deep_research_task, payload, "deep_research", outbox_id=outbox_id, owner_id=get_current_owner(request))


@router.post("/research/modify", status_code=202)
def modify_recommendation(body: ModifyRequest, request: Request) -> JSONResponse:
    """AI-modify formulas: async CRAG + recommend (subscribe via GET /api/tasks/{id}/stream)."""
    from ..domain.research_query import build_research_query
    from ..middleware.api_auth import get_current_owner

    req = body.requirement.model_copy(deep=True)
    note = f"[AI modify] {body.modify_prompt}"
    req.notes = f"{req.notes}\n{note}".strip() if req.notes else note

    base_formulas = list(body.base_formulas)
    if body.base_formulation is not None:
        base_formulas.insert(0, body.base_formulation)
    if base_formulas and req.active_formulation is None:
        req.active_formulation = base_formulas[0]

    augmented_query = build_research_query(
        f"{body.query or req.headline()} {body.modify_prompt}".strip(),
        req,
    )
    payload = {
        "topic": augmented_query,
        "requirement": req.model_dump(),
        "sources": [s.model_dump() for s in body.sources],
        "query": augmented_query,
        "modify_prompt": body.modify_prompt,
        "base_formulas": [f.model_dump() for f in base_formulas],
        "n": body.n,
    }
    return submit(run_recommend_task, payload, "recommend", owner_id=get_current_owner(request))


@router.get("/research/expand", response_model=ExpandedQuery, deprecated=True)
def expand_research_query(topic: str = Query(..., min_length=1)) -> ExpandedQuery:
    return QueryExpander().expand(topic)


# ── RAG backend status ──────────────────────────────────────────────────

@router.get("/research/rag/status")
def rag_status() -> dict:
    """Return current RAG retrieval backend and formulation mode."""
    from ..config import get_settings
    from ..services.colbert_store import active_backend, colbert_available_gpu

    s = get_settings()
    return {
        "backend": active_backend(s),
        "formulation_mode": s.formulation_mode,
        "gpu_enabled": s.gpu_enabled,
        "gpu_available": colbert_available_gpu(s),
        "rag_backend_setting": s.rag_backend,
    }

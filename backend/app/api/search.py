"""POST /api/search — Multi-source evidence retrieval (single-shot).
POST /api/search/stream — Incremental search; returns a task handle the client
     polls so it can render results while the search keeps going.
GET  /api/search/status — Per-source availability check (no network requests).
GET  /api/search/expand — Query expansion debug endpoint.
"""
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from ..domain.schemas import Evidence, Requirement
from ..services import literature
from ..services.deep_research import ExpandedQuery, QueryExpander
from ..worker.tasks import task_manager

router = APIRouter()


class SearchRequest(BaseModel):
    query: str = ""
    source_types: list[str] = Field(default_factory=list)
    requirement: Requirement | None = None
    limit_per_source: int = 50
    total_limit: int = 300


def _effective_source_types(request_types: list[str]) -> list[str]:
    from ..config import get_settings

    if request_types:
        return request_types
    return list(get_settings().federated_sources)


class TaskHandle(BaseModel):
    task_id: str
    poll_url: str


class SourceStatus(BaseModel):
    available: bool
    offline_fallback: bool = False
    reason: str | None = None
    hint: str | None = None


class SearchResponse(BaseModel):
    evidence: list[Evidence]
    total: int
    source_status: dict[str, SourceStatus] = {}


def _build_status() -> dict[str, SourceStatus]:
    raw = literature.get_source_availability()
    return {k: SourceStatus(**v) for k, v in raw.items()}


@router.get("/search/status")
def source_status() -> dict[str, SourceStatus]:
    """Lightweight availability check — no retrieval, no network requests.

    Called by the frontend on component mount so status badges appear before
    the user runs a search.
    """
    return _build_status()


@router.post("/search", response_model=SearchResponse)
def search_sources(req: SearchRequest):
    types = _effective_source_types(req.source_types)
    evidence = literature.search_by_types(
        query=req.query,
        source_types=types,
        req=req.requirement,
        limit_per_source=req.limit_per_source,
        total_limit=req.total_limit,
    )
    return SearchResponse(
        evidence=evidence,
        total=len(evidence),
        source_status=_build_status(),
    )


@router.post("/search/stream", response_model=TaskHandle)
def search_stream(req: SearchRequest) -> TaskHandle:
    """Kick off an incremental search; poll GET /api/tasks/{id} for growing results.

    Results accumulate round by round until no source turns up new related
    evidence (no fixed time budget), capped at ``total_limit`` and ranked by
    relevance to the query.
    """
    task_id = task_manager.submit_search(
        query=req.query,
        source_types=_effective_source_types(req.source_types),
        req=req.requirement,
        total_limit=req.total_limit,
        per_source_cap=req.limit_per_source,
    )
    return TaskHandle(task_id=task_id, poll_url=f"/api/tasks/{task_id}")


@router.get("/search/expand", response_model=ExpandedQuery)
def expand_search_query(topic: str = Query(..., min_length=1)) -> ExpandedQuery:
    """将自然语言主题扩展为结构化检索查询（QueryExpander）。"""
    return QueryExpander().expand(topic)

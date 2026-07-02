"""POST /api/search — Multi-source evidence retrieval (single-shot).
POST /api/search/stream — Incremental search; returns a task handle the client
     polls so it can render results while the search keeps going.
GET  /api/search/status — Per-source availability check (no network requests).
GET  /api/search/expand — Query expansion debug endpoint.
"""
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from ..domain.schemas import Evidence, Requirement
from ..services import literature
from ..services.deep_research import ExpandedQuery, QueryExpander
from ..worker.tasks import run_search_task
from .tasks import accepted_response

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
    stream_url: str
    status_url: str


class SourceStatus(BaseModel):
    available: bool
    offline_fallback: bool = False
    reason: str | None = None
    hint: str | None = None


class SearchResponse(BaseModel):
    evidence: list[Evidence]
    total: int
    source_status: dict[str, SourceStatus] = {}
    used_seed_fallback: bool = False


def _used_seed_fallback(evidence: list[Evidence]) -> bool:
    return any(e.is_seed_corpus for e in evidence)


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


@router.post("/search", response_model=SearchResponse, deprecated=True)
def search_sources(req: SearchRequest):
    """同步一次性检索（legacy）。前端请使用 ``POST /api/search/stream`` 增量检索。"""
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
        used_seed_fallback=_used_seed_fallback(evidence),
    )


@router.post("/search/stream", status_code=202)
def search_stream(req: SearchRequest) -> JSONResponse:
    async_result = run_search_task.delay({
        "query": req.query,
        "source_types": _effective_source_types(req.source_types),
        "requirement": req.requirement.model_dump() if req.requirement else None,
        "total_limit": req.total_limit,
        "per_source_cap": req.limit_per_source,
    })
    return accepted_response(async_result.id, "search")


@router.get("/search/expand")
def expand_search_query(topic: str = Query(..., min_length=1)) -> dict:
    """将自然语言主题扩展为结构化检索查询（QueryExpander）。"""
    from ..services.deep_research.query_expander import prepare_search_queries

    sq = prepare_search_queries(topic)
    return {
        **sq.expanded.model_dump(),
        "rank_q": sq.rank_q,
        "patent_q": sq.patent_q,
        "western_q": sq.western_q,
        "chinese_q": sq.chinese_q,
        "ipc_codes": list(sq.ipc_codes),
    }

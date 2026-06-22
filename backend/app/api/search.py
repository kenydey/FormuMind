"""POST /api/search — Multi-source evidence retrieval.
GET  /api/search/status — Per-source availability check (no network requests).
"""
from fastapi import APIRouter
from pydantic import BaseModel
from ..domain.schemas import Evidence, Requirement
from ..services import literature

router = APIRouter()


class SearchRequest(BaseModel):
    query: str = ""
    source_types: list[str] = ["patents", "literature"]
    requirement: Requirement | None = None
    limit_per_source: int = 5


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
    evidence = literature.search_by_types(
        query=req.query,
        source_types=req.source_types,
        req=req.requirement,
        limit_per_source=req.limit_per_source,
    )
    return SearchResponse(
        evidence=evidence,
        total=len(evidence),
        source_status=_build_status(),
    )

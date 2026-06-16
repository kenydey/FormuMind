"""POST /api/search — Multi-source evidence retrieval."""
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


class SearchResponse(BaseModel):
    evidence: list[Evidence]
    total: int


@router.post("/search", response_model=SearchResponse)
def search_sources(req: SearchRequest):
    evidence = literature.search_by_types(
        query=req.query,
        source_types=req.source_types,
        req=req.requirement,
        limit_per_source=req.limit_per_source,
    )
    return SearchResponse(evidence=evidence, total=len(evidence))

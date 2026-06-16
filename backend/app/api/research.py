"""Research endpoint: retrieve prior art and produce recommended formulations."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import Field

from ..domain.schemas import Evidence, Requirement, ResearchResult
from ..pipeline import workflow

router = APIRouter(prefix="/api", tags=["research"])


class ResearchRequest(Requirement):
    """Extends Requirement with optional pre-loaded sources from the frontend.

    When ``sources`` is non-empty, internal patent/literature retrieval is
    skipped and the supplied evidence is used directly.  This allows the
    /api/search and /api/ingest endpoints to feed evidence into the research
    pipeline without a redundant round-trip.

    Fully backward-compatible: a plain Requirement JSON body is valid here
    because ``sources`` defaults to an empty list.
    """

    # Pre-loaded sources from frontend; if non-empty, skip internal retrieval.
    sources: list[Evidence] = Field(default_factory=list)


@router.post("/research", response_model=ResearchResult)
def start_research(body: ResearchRequest) -> ResearchResult:
    """Run literature/patent retrieval + RAG + recommendation for a requirement."""
    req = Requirement(**{k: v for k, v in body.model_dump().items() if k != "sources"})
    pre_sources = body.sources if body.sources else None
    return workflow.run_research(req, pre_sources=pre_sources)

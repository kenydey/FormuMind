"""Research endpoint: retrieve prior art and produce recommended formulations."""
from __future__ import annotations

from fastapi import APIRouter

from ..domain.schemas import Requirement, ResearchResult
from ..pipeline import workflow

router = APIRouter(prefix="/api", tags=["research"])


@router.post("/research", response_model=ResearchResult)
def start_research(requirement: Requirement) -> ResearchResult:
    """Run literature/patent retrieval + RAG + recommendation for a requirement."""
    return workflow.run_research(requirement)

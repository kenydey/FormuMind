"""Research endpoint: retrieve prior art and produce recommended formulations."""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..domain.schemas import DeepResearchRequest, Evidence, Requirement, ResearchResult
from ..pipeline import workflow
from ..services.deep_research import ExpandedQuery, QueryExpander
from ..worker.tasks import task_manager

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


class DeepResearchHandle(BaseModel):
    task_id: str
    poll_url: str


@router.post("/research/deep", response_model=DeepResearchHandle)
def start_deep_research(body: DeepResearchRequest) -> DeepResearchHandle:
    """Kick off async knowledge-cohort deep research; poll GET /api/tasks/{id}.

    Returns a task handle immediately (the cohort retrieval + multi-agent
    synthesis is long-running). The result is a ``ComprehensiveReport``.
    """
    req = Requirement(**{k: v for k, v in body.model_dump().items() if k != "topic"})
    topic = body.topic or req.headline()
    task_id = task_manager.submit_comprehensive_research(topic, req)
    return DeepResearchHandle(task_id=task_id, poll_url=f"/api/tasks/{task_id}")


@router.get("/research/expand", response_model=ExpandedQuery)
def expand_research_query(topic: str = Query(..., min_length=1)) -> ExpandedQuery:
    """调试端点：将自然语言主题扩展为结构化检索查询（QueryExpander）。"""
    return QueryExpander().expand(topic)

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
    skipped and the supplied evidence is used directly.
    """

    sources: list[Evidence] = Field(default_factory=list)


@router.post("/research", response_model=ResearchResult)
def start_research(body: ResearchRequest) -> ResearchResult:
    """同步配方推荐：检索 + RAG + 推荐（可用预加载 sources 跳过重复检索）。"""
    req = Requirement(**{k: v for k, v in body.model_dump().items() if k != "sources"})
    pre_sources = body.sources if body.sources else None
    return workflow.run_research(req, pre_sources=pre_sources)


class DeepResearchHandle(BaseModel):
    task_id: str
    poll_url: str


@router.post("/research/deep", response_model=DeepResearchHandle)
def start_deep_research(body: DeepResearchRequest) -> DeepResearchHandle:
    """异步深度研究：多源检索 + RAG + 引用报告。轮询 GET /api/tasks/{id}。"""
    req = Requirement(**{
        k: v for k, v in body.model_dump().items()
        if k not in ("topic", "source_types")
    })
    topic = body.topic or req.headline()
    task_id = task_manager.submit_comprehensive_research(
        topic, req, source_types=body.source_types
    )
    return DeepResearchHandle(task_id=task_id, poll_url=f"/api/tasks/{task_id}")


@router.get("/research/expand", response_model=ExpandedQuery, deprecated=True)
def expand_research_query(topic: str = Query(..., min_length=1)) -> ExpandedQuery:
    """兼容别名 — 请优先使用 GET /api/search/expand。"""
    return QueryExpander().expand(topic)

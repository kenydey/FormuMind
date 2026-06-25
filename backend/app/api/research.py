"""Research endpoint: CRAG graph + SSE streaming."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from ..domain.schemas import ComprehensiveReport, Evidence, Requirement, ResearchResult
from ..pipeline import workflow
from ..pipeline.research_graph import run_research_graph
from ..services.deep_research import ExpandedQuery, QueryExpander
from ..services.federated_search import FederatedSearchEngine

router = APIRouter(prefix="/api", tags=["research"])


class ResearchRequest(Requirement):
    """Extends Requirement with optional pre-loaded sources for indexing before CRAG."""

    sources: list[Evidence] = Field(default_factory=list)
    source_types: list[str] = Field(default_factory=list, deprecated=True)
    query: str = ""


class ResearchStreamRequest(BaseModel):
    topic: str = Field(min_length=1)
    requirement: Requirement
    sources: list[Evidence] = Field(default_factory=list)
    query: str = ""


class ResearchStreamEvent(BaseModel):
    event: str
    stage: str | None = None
    message: str = ""
    data: dict | None = None


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
    return workflow.run_research(
        req,
        pre_sources=pre_sources,
        query=body.query,
    )


@router.post("/research/stream")
async def research_stream(body: ResearchStreamRequest) -> StreamingResponse:
    """SSE 深度研究：retrieve → grade → fallback → generate → recommend。"""

    async def _generator() -> AsyncIterator[str]:
        queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def event_cb(stage: str, message: str, partial: dict | None = None) -> None:
            payload: dict = {"stage": stage, "message": message}
            if partial:
                payload.update(partial)
            loop.call_soon_threadsafe(queue.put_nowait, ("stage", payload))

        req = body.requirement
        topic = body.topic or req.headline()
        query = body.query or topic

        def _run() -> None:
            try:
                state = run_research_graph(
                    topic=topic,
                    req=req,
                    query=query,
                    pre_index=body.sources or None,
                    progress_cb=event_cb,
                )
                grounded = state.get("grounded_evidence") or []
                report = ComprehensiveReport(
                    topic=topic,
                    report_markdown=state.get("report_markdown") or state.get("answer") or "",
                    citations=state.get("citations") or grounded,
                    candidates=state.get("recommended") or [],
                    web_count=0,
                    kb_count=len(grounded),
                    engine=state.get("recommend_engine") or "offline",
                )
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    (
                        "result",
                        {
                            "report": report.model_dump(),
                            "grounded_evidence": [e.model_dump() for e in grounded],
                        },
                    ),
                )
            except Exception as exc:
                logger.exception("research stream failed")
                loop.call_soon_threadsafe(queue.put_nowait, ("error", {"detail": str(exc)}))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        await loop.run_in_executor(None, _run)

        while True:
            item = await queue.get()
            if item is None:
                break
            event_name, data = item
            yield f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/research/kb/refresh")
def refresh_knowledge_base(query: str = Query(..., min_length=1)) -> dict:
    """Explicit federated fetch → ColBERT index (replaces per-source UI toggles)."""
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
    """兼容别名 — 请优先使用 GET /api/search/expand。"""
    return QueryExpander().expand(topic)

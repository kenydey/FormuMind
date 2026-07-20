"""POST /api/chat — Q&A grounded in loaded sources."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from ..domain.kg_schemas import EntityResolutionSummary, KGRetrieveStats
from ..domain.schemas import Evidence
from ..services.llm import answer_question
from ..services.rag import active_rag_backend

logger = logging.getLogger(__name__)

router = APIRouter()


def _clamp_relevance(value: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return 0.5
    if n != n:  # NaN
        return 0.5
    return max(0.0, min(1.0, n))


def _sanitize_evidence(ev: Evidence) -> Evidence:
    """Clamp relevance and fill required strings so stale project rows never 422."""
    identifier = (ev.identifier or ev.title or "source").strip() or "source"
    title = (ev.title or identifier).strip() or identifier
    snippet = (ev.snippet or "").strip()
    return ev.model_copy(
        update={
            "source": (ev.source or "local").strip() or "local",
            "identifier": identifier,
            "title": title,
            "snippet": snippet or title,
            "relevance": _clamp_relevance(ev.relevance),
        }
    )


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    sources: list[Evidence] = []
    domain: str | None = None
    project_id: str | None = None
    include_entity_resolution: bool = False

    @field_validator("sources", mode="before")
    @classmethod
    def _coerce_sources(cls, raw: object) -> object:
        if not isinstance(raw, list):
            return raw
        out: list[dict] = []
        for item in raw:
            if isinstance(item, Evidence):
                out.append(_sanitize_evidence(item).model_dump())
            elif isinstance(item, dict):
                data = dict(item)
                if "relevance" in data:
                    data["relevance"] = _clamp_relevance(data.get("relevance", 0.5))
                try:
                    out.append(_sanitize_evidence(Evidence.model_validate(data)).model_dump())
                except Exception:
                    continue
            else:
                continue
        return out


class ChatResponse(BaseModel):
    answer: str
    citations: list[Evidence]
    rag_backend: str = "tfidf"  # which retrieval backend served the citations
    kb_chunks_used: int = 0  # persistent-KB chunks merged into the grounding set
    entity_resolution: EntityResolutionSummary | None = None
    kg_retrieval_stats: KGRetrieveStats | None = None


def _augment_with_kb(
    question: str,
    sources: list[Evidence],
    *,
    project_id: str | None = None,
    include_entity_resolution: bool = False,
) -> tuple[list[Evidence], int, EntityResolutionSummary | None, KGRetrieveStats | None]:
    """Merge persistent-KB / KG retrieval into the grounding set."""
    from ..config import get_settings
    from ..services import kb_index

    settings = get_settings()
    resolution: EntityResolutionSummary | None = None
    kg_stats: KGRetrieveStats | None = None

    if settings.kg_enabled:
        from ..services.kg import retrieve as kg_retrieve
        from ..services.kg.retrieval import build_resolution_summary

        result = kg_retrieve(
            question,
            project_id=project_id,
            pre_evidence=sources,
            k_semantic=settings.kb_chat_top_k,
        )
        if include_entity_resolution:
            resolution = build_resolution_summary(question)
        kg_stats = result.stats
        added = max(0, len(result.evidence) - len(sources))
        return result.evidence, added, resolution, kg_stats

    if not settings.kb_v2_enabled:
        return sources, 0, resolution, kg_stats
    hits = kb_index.search_chunks(question, k=settings.kb_chat_top_k, project_id=project_id)
    if not hits:
        return sources, 0, resolution, kg_stats
    seen = {ev.identifier for ev in sources}
    added = [h for h in hits if h.identifier not in seen]
    return sources + added, len(added), resolution, kg_stats


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    try:
        sources = [_sanitize_evidence(ev) for ev in req.sources]
        sources, kb_used, entity_resolution, kg_stats = _augment_with_kb(
            req.question.strip(),
            sources,
            project_id=req.project_id,
            include_entity_resolution=req.include_entity_resolution,
        )
        answer, citations = answer_question(
            question=req.question.strip(),
            sources=sources,
            domain=req.domain,
        )
        return ChatResponse(
            answer=answer,
            citations=citations,
            rag_backend=active_rag_backend(),
            kb_chunks_used=kb_used,
            entity_resolution=entity_resolution,
            kg_retrieval_stats=kg_stats,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("chat failed")
        raise HTTPException(status_code=500, detail=f"问答处理失败：{exc}") from exc

"""POST /api/chat — Q&A grounded in loaded sources."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

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


def _augment_with_kb(question: str, sources: list[Evidence]) -> tuple[list[Evidence], int]:
    """Merge top persistent-KB chunks into the grounding set (KB v2).

    The client's sources keep priority; KB hits already present (same
    identifier) are skipped. Empty KB / disabled flag → unchanged."""
    from ..config import get_settings
    from ..services import kb_index

    settings = get_settings()
    if not settings.kb_v2_enabled:
        return sources, 0
    hits = kb_index.search_chunks(question, k=settings.kb_chat_top_k)
    if not hits:
        return sources, 0
    seen = {ev.identifier for ev in sources}
    added = [h for h in hits if h.identifier not in seen]
    return sources + added, len(added)


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    try:
        sources = [_sanitize_evidence(ev) for ev in req.sources]
        sources, kb_used = _augment_with_kb(req.question.strip(), sources)
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
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("chat failed")
        raise HTTPException(status_code=500, detail=f"问答处理失败：{exc}") from exc

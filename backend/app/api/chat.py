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


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    try:
        sources = [_sanitize_evidence(ev) for ev in req.sources]
        answer, citations = answer_question(
            question=req.question.strip(),
            sources=sources,
            domain=req.domain,
        )
        return ChatResponse(
            answer=answer, citations=citations, rag_backend=active_rag_backend()
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("chat failed")
        raise HTTPException(status_code=500, detail=f"问答处理失败：{exc}") from exc

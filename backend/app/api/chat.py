"""POST /api/chat — Q&A grounded in loaded sources (Chat P0)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import field_validator

from ..domain.chat_schemas import ChatRequest, ChatResponse, ChatTurn, StructuredAnswer
from ..domain.kg_schemas import EntityResolutionSummary, KGRetrieveStats
from ..domain.schemas import Evidence
from ..services.chat_claims import build_sourced_claims
from ..services.chat_clarify import apply_assumption_to_structured, detect_clarification
from ..services.chat_context import rewrite_query, trim_history
from ..services.chat_structured import generate_structured_answer
from ..services.llm import answer_question
from ..services.rag import active_rag_backend

logger = logging.getLogger(__name__)

router = APIRouter()


def _clamp_relevance(value: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return 0.5
    if n != n:
        return 0.5
    return max(0.0, min(1.0, n))


def _sanitize_evidence(ev: Evidence) -> Evidence:
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


class ChatRequestValidated(ChatRequest):
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
        return out

    @field_validator("history", mode="before")
    @classmethod
    def _coerce_history(cls, raw: object) -> object:
        if not isinstance(raw, list):
            return raw
        from ..config import get_settings

        cap = get_settings().chat_history_max_turns
        items = raw[-cap:] if len(raw) > cap else raw
        out: list[dict] = []
        for item in items:
            if isinstance(item, ChatTurn):
                out.append(item.model_dump())
            elif isinstance(item, dict):
                try:
                    out.append(ChatTurn.model_validate(item).model_dump())
                except Exception:
                    continue
        return out


def _augment_with_kb(
    question: str,
    sources: list[Evidence],
    *,
    project_id: str | None = None,
    include_entity_resolution: bool = False,
) -> tuple[list[Evidence], int, EntityResolutionSummary | None, KGRetrieveStats | None]:
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


def _ensure_answer(text: str | None, *, fallback: str = "暂无可用回答。") -> str:
    cleaned = (text or "").strip()
    return cleaned or fallback


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequestValidated):
    try:
        from ..config import get_settings

        settings = get_settings()
        question = req.question.strip()
        history = trim_history(req.history)

        retrieval_query, rewritten_query = rewrite_query(
            question,
            history,
            req.clarified_entities,
            settings=settings,
        )

        sources = [_sanitize_evidence(ev) for ev in req.sources]
        sources, kb_used, entity_resolution, kg_stats = _augment_with_kb(
            retrieval_query,
            sources,
            project_id=req.project_id,
            include_entity_resolution=req.include_entity_resolution,
        )

        clarification = detect_clarification(
            question,
            history,
            req.clarified_entities,
            settings=settings,
        )

        structured: StructuredAnswer | None = None
        citations: list[Evidence]

        if req.response_format == "structured" and settings.chat_structured_enabled:
            structured, struct_err = generate_structured_answer(
                question,
                sources,
                history=history,
                domain=req.domain,
                settings=settings,
            )
            if structured is not None:
                structured = apply_assumption_to_structured(structured, clarification)
                answer = _ensure_answer(structured.summary)
                citations = sources[: min(8, len(sources))]
            else:
                logger.warning("structured chat fallback: %s", struct_err)
                answer, citations = answer_question(
                    question,
                    sources,
                    domain=req.domain,
                    history=history,
                )
                answer = _ensure_answer(answer)
        else:
            answer, citations = answer_question(
                question,
                sources,
                domain=req.domain,
                history=history,
            )
            answer = _ensure_answer(answer)

        if clarification and clarification.possible_meanings and "按" not in answer:
            hint = clarification.possible_meanings[0]
            answer = f"{answer}\n\n（默认按「{hint}」理解；如需其他含义请说明。）"

        sourced_claims = build_sourced_claims(
            question,
            answer,
            citations,
            structured=structured,
            settings=settings,
        )

        return ChatResponse(
            answer=answer,
            citations=[_sanitize_evidence(c) for c in citations],
            rag_backend=active_rag_backend(),
            kb_chunks_used=kb_used,
            entity_resolution=entity_resolution,
            kg_retrieval_stats=kg_stats,
            structured=structured,
            clarification=clarification,
            rewritten_query=rewritten_query,
            sourced_claims=sourced_claims,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("chat failed")
        raise HTTPException(status_code=500, detail=f"问答处理失败：{exc}") from exc

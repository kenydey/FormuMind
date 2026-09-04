"""POST /api/chat — Q&A grounded in loaded sources (Chat P0)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import field_validator

from ..domain.chat_schemas import (
    ChatRequest,
    ChatResponse,
    ChatTurn,
    StructuredAnswer,
    structure_retrieval_context,
)
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
        # Cap the number of supplied sources to bound prompt size / cost.
        # NOTE: ChatRequest.sources lives in domain/chat_schemas.py (outside
        # this agent's file list); the hard Field(max_length=50) constraint
        # should be added there when that file is touched.
        if len(raw) > 50:
            logger.warning("chat sources truncated: %d -> 50", len(raw))
            raw = raw[:50]
        out: list[dict] = []
        dropped = 0
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
                    dropped += 1
                    continue
        if dropped:
            logger.warning("chat sources: %d invalid item(s) discarded", dropped)
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
    import time as _time
    _t0 = _time.time()
    _marks: list[str] = []

    def _mark(name: str) -> None:
        _marks.append(f"{name}={_time.time() - _t0:.1f}s")

    try:
        from ..config import get_settings

        settings = get_settings()
        question = req.question.strip()
        history = trim_history(req.history)
        _mark("parse")

        retrieval_query, rewritten_query = rewrite_query(
            question,
            history,
            req.clarified_entities,
            settings=settings,
        )

        # 结构图上下文：相似材料名并入检索 query（增强命中），不污染展示文案。
        struct_ctx = structure_retrieval_context(req.structure)
        if struct_ctx:
            retrieval_query = f"{struct_ctx} {retrieval_query}".strip()
            rewritten_query = rewritten_query or retrieval_query

        sources = [_sanitize_evidence(ev) for ev in req.sources]
        sources, kb_used, entity_resolution, kg_stats = _augment_with_kb(
            retrieval_query,
            sources,
            project_id=req.project_id,
            include_entity_resolution=req.include_entity_resolution,
        )
        _mark("kb_augment")

        clarification = detect_clarification(
            question,
            history,
            req.clarified_entities,
            settings=settings,
        )
        _mark("clarify")

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
                    structure=req.structure,
                )
                answer = _ensure_answer(answer)
            _mark("answer")
        else:
            answer, citations = answer_question(
                question,
                sources,
                domain=req.domain,
                history=history,
                structure=req.structure,
            )
            answer = _ensure_answer(answer)
            _mark("answer")

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
        _mark("claims")
        logger.info("chat 耗时分解: %s", " | ".join(_marks))

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
        _mark("FAILED")
        logger.info("chat 耗时分解(失败): %s | %s", " | ".join(_marks), str(exc)[:200])
        logger.exception("chat failed")
        raise HTTPException(status_code=500, detail="问答处理失败") from exc


# ── SSE 流式问答(2026-09-04)─────────────────────────────────────────────
# 事件协议(data: JSON 一行一个):
#   {"type":"phase","phase":"retrieval|answering|claims"}
#   {"type":"meta","kb_used":N,"rewritten_query":...}
#   {"type":"token","delta":"..."}                    # 主回答增量
#   {"type":"done", 完整 ChatResponse 字段}            # 收尾(含 citations/claims)
#   {"type":"error","message":"..."}
# 旧 /api/chat 保留(兼容/测试/结构化完整回退)。


def _stream_answer_plan(req: "ChatRequestValidated", settings):
    """同步准备: 改写/检索/澄清/召回 → (question, prompt, ctx)。

    与 /api/chat 的准备逻辑一致(kb_augment + BM25 召回 top-k 直取,
    不做 LLM 二次精排——2026-09-04 实测 rerank 30-76s/问, 收益边际)。
    """
    from ..services import kb_index  # noqa: F401 (warm imports)
    from ..services.chat_context import rewrite_query, trim_history
    from ..services.chat_clarify import detect_clarification
    from ..services.llm import _chat_prompt
    from ..services.rag import build_store

    question = (req.question or "").strip()
    history = trim_history(req.history)

    retrieval_query, rewritten_query = rewrite_query(
        question, history, req.clarified_entities, settings=settings
    )
    struct_ctx = structure_retrieval_context(req.structure)
    if struct_ctx:
        retrieval_query = f"{struct_ctx} {retrieval_query}".strip()
        rewritten_query = rewritten_query or retrieval_query

    sources = [_sanitize_evidence(ev) for ev in req.sources]
    sources, kb_used, entity_resolution, kg_stats = _augment_with_kb(
        retrieval_query,
        sources,
        project_id=req.project_id,
        include_entity_resolution=req.include_entity_resolution,
    )

    clarification = detect_clarification(
        question, history, req.clarified_entities, settings=settings
    )

    # BM25 召回(与 answer_question 同款; 不再 LLM rerank)。
    store = build_store()
    store.ingest(sources)
    candidates_n = min(settings.chat_rerank_candidates, max(1, len(sources)))
    recalled = store.query(retrieval_query, k=candidates_n) or sources[:candidates_n]
    relevant = recalled[: settings.chat_rerank_top_k]

    prompt = _chat_prompt(
        question, relevant, req.domain, history=history, structure=req.structure
    )
    return {
        "question": question,
        "prompt": prompt,
        "sources": sources,
        "kb_used": kb_used,
        "entity_resolution": entity_resolution,
        "kg_stats": kg_stats,
        "clarification": clarification,
        "rewritten_query": rewritten_query,
    }


def _sse(obj: dict) -> str:
    import json

    return f"data: {json.dumps(obj, ensure_ascii=False, default=str)}\n\n"


@router.post("/chat/stream")
async def chat_stream(req: "ChatRequestValidated"):
    """SSE 流式问答: 检索阶段提示 → 主回答逐 token → done(含引用/claims)。

    结构化(StructuredAnswer)请求暂不走 token 流(需整包 JSON 校验),
    完整生成后单发 done; markdown 请求全流式。
    """
    import asyncio
    import threading
    from ..config import get_settings
    from ..services.llm import (
        _chat_prompt,
        _openai_compatible_stream,
        effective_setting as _es,
        _resolve_openai_base_url,
    )
    from ..domain.chat_schemas import ChatResponse

    settings = get_settings()
    provider = _es(settings, "llm_provider")
    api_key = settings.get_active_api_key() or ""

    async def gen():
        if not api_key:
            yield _sse({"type": "error", "message": "未配置 LLM API Key"})
            return

        try:
            yield _sse({"type": "phase", "phase": "retrieval"})
            plan = await asyncio.to_thread(_stream_answer_plan, req, settings)
            question = plan["question"]
            prompt = plan["prompt"]
            sources = plan["sources"]
            kb_used = plan["kb_used"]

            yield _sse(
                {
                    "type": "meta",
                    "kb_used": kb_used,
                    "rewritten_query": plan["rewritten_query"],
                    "source_count": len(sources),
                }
            )
        except Exception as exc:
            logger.warning("chat/stream 准备失败: %s", exc)
            yield _sse({"type": "error", "message": f"检索失败: {str(exc)[:200]}"})
            return

        # 结构化请求 → 整包答案(非 token 流)。
        try:
            if req.response_format == "structured" and settings.chat_structured_enabled:
                from ..services.chat_structured import generate_structured_answer

                structured, struct_err = await asyncio.to_thread(
                    generate_structured_answer,
                    question,
                    plan["sources"],
                    history=req.history,
                    domain=req.domain,
                    settings=settings,
                )
                if structured is None:
                    logger.warning("structured stream fallback: %s", struct_err)
                    structured = None
                answer = ""
                citations: list = []
                if structured is not None:
                    structured = apply_assumption_to_structured(
                        structured, plan["clarification"]
                    )
                    assert structured is not None
                    answer = _ensure_answer(structured.summary)
                    citations = plan["sources"][: min(8, len(plan["sources"]))]
                yield _sse(
                    {
                        "type": "done",
                        "answer": answer,
                        "citations": [_sanitize_evidence(c) for c in citations],
                        "rag_backend": active_rag_backend(),
                        "kb_chunks_used": kb_used,
                        "structured": structured,
                        "clarification": plan["clarification"],
                        "rewritten_query": plan["rewritten_query"],
                    }
                )
                return

            # markdown 主回答 — token 流。
            yield _sse({"type": "phase", "phase": "answering"})
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue = asyncio.Queue(maxsize=256)
            result_holder: dict = {}

            def worker() -> None:
                def on_delta(piece: str) -> None:
                    try:
                        loop.call_soon_threadsafe(queue.put_nowait, ("tok", piece))
                    except RuntimeError:
                        pass  # loop 已关(客户端断开)

                try:
                    base_url = _resolve_openai_base_url(
                        provider, _es(settings, "llm_base_url")
                    )
                    text = _openai_compatible_stream(
                        prompt,
                        api_key,
                        _es(settings, "llm_model"),
                        2048,
                        base_url,
                        on_delta=on_delta,
                        disable_thinking=True,
                    )
                    result_holder["text"] = text
                except Exception as exc:  # noqa: BLE001
                    result_holder["error"] = str(exc)[:300]
                finally:
                    try:
                        loop.call_soon_threadsafe(queue.put_nowait, ("end", None))
                    except RuntimeError:
                        pass

            t = threading.Thread(target=worker, daemon=True)
            t.start()

            parts: list[str] = []
            try:
                while True:
                    kind, payload = await asyncio.wait_for(queue.get(), timeout=120)
                    if kind == "tok":
                        parts.append(payload)
                        yield _sse({"type": "token", "delta": payload})
                    else:
                        break
            except asyncio.TimeoutError:
                yield _sse(
                    {
                        "type": "error",
                        "message": "回答超时(120s 无输出), 请重试",
                    }
                )
                return
            finally:
                t.join(timeout=0.2)

            if "error" in result_holder:
                err = result_holder["error"]
                logger.warning("chat/stream LLM 失败: %s", err)
                yield _sse({"type": "error", "message": f"生成失败: {err}"})
                return

            answer = "".join(parts).strip()
            if not answer:
                answer = result_holder.get("text") or ""

            # claims 收尾(12s 硬超时 → offline 降级)。
            yield _sse({"type": "phase", "phase": "claims"})
            claims = None
            if settings.chat_claim_check_enabled and answer:
                try:
                    claims = await asyncio.to_thread(
                        build_sourced_claims,
                        question,
                        answer,
                        plan["sources"],
                        structured=None,
                        settings=settings,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("chat/stream claims 失败: %s", exc)
                    claims = None

            yield _sse(
                {
                    "type": "done",
                    "answer": answer,
                    "citations": [
                        _sanitize_evidence(c) for c in plan["sources"][: min(8, len(plan["sources"]))]
                    ],
                    "rag_backend": active_rag_backend(),
                    "kb_chunks_used": kb_used,
                    "entity_resolution": plan["entity_resolution"],
                    "kg_retrieval_stats": plan["kg_stats"],
                    "clarification": plan["clarification"],
                    "rewritten_query": plan["rewritten_query"],
                    "sourced_claims": claims,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("chat/stream failed")
            yield _sse({"type": "error", "message": "问答处理失败"})
            return

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

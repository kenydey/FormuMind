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
from ..services.chat_claims import (
    build_sources_audit,
    build_sourced_claims,
    verify_answer_claims,
)
from ..services.chat_clarify import apply_assumption_to_structured, detect_clarification
from ..services.chat_context import rewrite_query, trim_history
from ..services.chat_structured import generate_structured_answer
from ..services.llm import answer_question
from ..services.rag import active_rag_backend

logger = logging.getLogger(__name__)

router = APIRouter()


# ── B-7 reviewer 显错契约 ──────────────────────────────────────────────
# evidence_reviewer.review_answer 在配置了 evidence_reviewer_model 后失败会
# 抛 ReviewerModelError（"不静默回退，避免'以为审了'"）。调用方必须单独
# 捕获并返回显式的 reviewer error 状态，绝不 debug-only 吞掉。


def _run_evidence_review(question, answer, evidence, settings):
    """调用 evidence_reviewer.review_answer，B-7 显错封装。

    - ReviewerModelError → ``{"status": "error", "reviewer_error": ...}``
      （配置专用 reviewer 模型后失败必须显错，客户端绝不能误以为"已审"）；
    - 其他异常 → None（fail-open，沿用旧行为）。
    """
    from ..services.evidence_reviewer import ReviewerModelError, review_answer

    try:
        return review_answer(question, answer, evidence, settings=settings)
    except ReviewerModelError as exc:
        logger.error("reviewer 模型失败: %s", exc)
        return {
            "status": "error",
            "reviewer_error": str(exc)[:500],
            "notes": [],
            "findings": [],
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("reviewer skipped: %s", exc)
        return None


def _reviewer_failed(review) -> bool:
    """reviewer 是否处于显错状态（B-7）；是则跳过 fix-loop 与 auto-review。"""
    return isinstance(review, dict) and bool(review.get("reviewer_error"))


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

    settings = get_settings()
    resolution: EntityResolutionSummary | None = None
    kg_stats: KGRetrieveStats | None = None
    wiki_added = 0

    # W3: blend Wiki condensed pages (flag-gated; prepended).
    try:
        from ..services.wiki.retrieve import blend_wiki_evidence

        sources, wiki_added = blend_wiki_evidence(question, sources)
    except Exception as exc:  # noqa: BLE001
        logger.debug("wiki chat blend skipped: %s", exc)

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
        added = max(0, len(result.evidence) - len(sources)) + wiki_added
        return result.evidence, added, resolution, kg_stats

    if not settings.kb_v2_enabled:
        return sources, wiki_added, resolution, kg_stats
    from ..services.kb_bilingual import search as bilingual_search

    hits = bilingual_search(
        question, k=settings.kb_chat_top_k, project_id=project_id
    )
    if not hits:
        return sources, wiki_added, resolution, kg_stats
    seen = {ev.identifier for ev in sources}
    added = [h for h in hits if h.identifier not in seen]
    return sources + added, len(added) + wiki_added, resolution, kg_stats


def _claims_evidence(evidence: list[Evidence]) -> list[Evidence]:
    """Strip Wiki rows so claims cannot cite compiled pages as Raw proof."""
    try:
        from ..services.wiki.retrieve import filter_raw_evidence

        return filter_raw_evidence(evidence)
    except Exception:
        return evidence


def _claims_and_audit(
    question: str,
    answer: str,
    citations: list[Evidence],
    *,
    structured: StructuredAnswer | None = None,
    settings=None,
):
    """Run claim verification once → sourced_claims + sources_audit (Wave D)."""
    from ..config import get_settings

    settings = settings or get_settings()
    evidence = _claims_evidence(citations)
    verified = verify_answer_claims(
        question,
        answer,
        evidence,
        structured=structured,
        settings=settings,
    )
    sourced_claims = build_sourced_claims(
        question,
        answer,
        evidence,
        structured=structured,
        settings=settings,
        verified=verified,
    )
    sources_audit = None
    if verified is not None and bool(
        getattr(settings, "sources_audit_enabled", True)
    ):
        try:
            sources_audit = build_sources_audit(
                evidence, verified=verified, enabled=True
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("sources_audit skipped: %s", exc)
            sources_audit = None
    return sourced_claims, sources_audit


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
        try:
            from ..services.connectors_builtin import gather_connector_evidence

            extra = gather_connector_evidence(
                retrieval_query,
                list(req.selected_connectors or []),
                settings=settings,
            )
            if extra:
                sources = sources + [_sanitize_evidence(e) for e in extra]
        except Exception as exc:  # noqa: BLE001
            logger.debug("connector enrich skipped: %s", exc)
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
        from ..services.evidence_synthesis import (
            evidence_mode_active,
            try_paperqa_answer,
            postprocess_evidence_answer,
        )

        use_evidence = evidence_mode_active(req.mode, settings)

        prompt_prefix = ""
        try:
            if getattr(settings, "chat_skills_runtime_enabled", True) and (
                use_evidence or req.selected_skills or req.selected_mcp_servers
            ):
                from ..services.evidence_synthesis import build_evidence_prompt_prefix

                prompt_prefix = build_evidence_prompt_prefix(
                    skill_ids=list(req.selected_skills or []),
                    mcp_server_ids=list(req.selected_mcp_servers or []),
                    settings=settings,
                    project_id=req.project_id,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("sync skill/mcp prefix skipped: %s", exc)

        # Phase 4 — Text2SQL hybrid routing: prepend deterministic SQL rows
        # to the answer prompt. Fail-open: hook never raises, SQL failure
        # falls back to the pure-literature path (prompt_prefix unchanged).
        sql_block, sql_prov = "", {"data_sources": ["kb_evidence"]}
        try:
            from ..services.text2sql import structured_data_block

            sql_block, sql_prov = structured_data_block(
                retrieval_query, settings=settings, project_id=req.project_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("text2sql chain hook skipped: %s", exc)
        if sql_block:
            prompt_prefix = (
                f"{sql_block}\n\n{prompt_prefix}" if prompt_prefix else sql_block
            )
        data_sources = list(sql_prov.get("data_sources") or ["kb_evidence"])

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
                    prompt_prefix=prompt_prefix or None,
                )
                answer = _ensure_answer(answer)
            _mark("answer")
        else:
            pq = try_paperqa_answer(question, sources) if use_evidence else None
            if pq:
                answer, citations = pq
                answer = _ensure_answer(answer)
            else:
                answer, citations = answer_question(
                    question,
                    sources,
                    domain=req.domain,
                    history=history,
                    structure=req.structure,
                    prompt_prefix=prompt_prefix or None,
                )
                answer = _ensure_answer(answer)
            _mark("answer")

        if clarification and clarification.possible_meanings and "按" not in answer:
            hint = clarification.possible_meanings[0]
            answer = f"{answer}\n\n（默认按「{hint}」理解；如需其他含义请说明。）"

        doi_results = None
        citation_expand = None
        evidence_provenance = None
        evidence_reviewer = None
        reviewer_fix = None
        mcp_permission_required = None
        mcp_tool_results = None
        if use_evidence or req.selected_skills or req.selected_mcp_servers:
            answer, emeta = postprocess_evidence_answer(answer, settings=settings)
            doi_results = emeta.get("doi_results")
            citation_expand = emeta.get("citation_expand") or None
            evidence_reviewer = _run_evidence_review(
                question, answer, _claims_evidence(citations), settings
            )
            if (
                evidence_reviewer
                and not _reviewer_failed(evidence_reviewer)
                and (evidence_reviewer.get("status") or "pass") != "pass"
            ):
                try:
                    from ..services.reviewer_fix_loop import run_fix_loop

                    def _repair(_q: str, auditor: str) -> str:
                        repaired, _ = answer_question(
                            f"{question}\n\n{auditor}\n\n请输出修订后的完整回答：",
                            sources,
                            domain=req.domain,
                            history=history,
                            structure=req.structure,
                        )
                        return _ensure_answer(repaired)

                    answer, reviewer_fix = run_fix_loop(
                        question=question,
                        answer=answer,
                        citations=_claims_evidence(citations),
                        review=evidence_reviewer,
                        settings=settings,
                        repair_fn=_repair,
                        max_rounds=3,
                        project_id=req.project_id,
                    )
                    if reviewer_fix and reviewer_fix.get("findings"):
                        evidence_reviewer = reviewer_fix["findings"]
                except Exception as exc:  # noqa: BLE001
                    logger.debug("reviewer fix-loop skipped: %s", exc)

        # MCP skill-doc inject is handled in evidence_synthesis; optional tool call path
        if req.selected_mcp_servers:
            try:
                from ..services.mcp_skill_docs import ensure_mcp_skill_docs
                from ..services.mcp_chat_tools import maybe_run_selected_mcp

                ensure_mcp_skill_docs(
                    server_ids=list(req.selected_mcp_servers or []),
                    settings=settings,
                    probe=True,
                )
                mcp_out = maybe_run_selected_mcp(
                    question,
                    list(req.selected_mcp_servers or []),
                    session_id=req.chat_session_id,
                    settings=settings,
                )
                mcp_tool_results = mcp_out.get("results")
                mcp_permission_required = mcp_out.get("mcp_permission_required")
            except Exception as exc:  # noqa: BLE001
                logger.debug("mcp chat selection skipped: %s", exc)

        sourced_claims, sources_audit = _claims_and_audit(
            question,
            answer,
            citations,
            structured=structured,
            settings=settings,
        )
        try:
            from ..services.scholar_helpers import build_evidence_provenance

            evidence_provenance = build_evidence_provenance(
                doi_results=doi_results,
                sourced_claims=sourced_claims,
                enabled=bool(getattr(settings, "evidence_provenance_enabled", True)),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("evidence_provenance skipped: %s", exc)
        _mark("claims")
        logger.info("chat 耗时分解: %s", " | ".join(_marks))

        # W2-8 (P1-12): turn-stop 自动审计；evidence 路径已内联 review 的 turn 跳过。
        if evidence_reviewer is None:
            _fire_auto_review(
                question=question,
                answer=answer,
                citations=_claims_evidence(citations),
                settings=settings,
                session_id=req.chat_session_id,
                project_id=req.project_id,
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
            sources_audit=sources_audit,
            mode=req.mode,
            doi_results=doi_results,
            citation_expand=citation_expand,
            evidence_provenance=evidence_provenance,
            evidence_reviewer=evidence_reviewer,
            reviewer_fix=reviewer_fix,
            mcp_permission_required=mcp_permission_required,
            mcp_tool_results=mcp_tool_results,
            data_sources=data_sources,
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
    try:
        from ..services.connectors_builtin import gather_connector_evidence

        extra = gather_connector_evidence(
            retrieval_query,
            list(req.selected_connectors or []),
            settings=settings,
        )
        if extra:
            sources = sources + [_sanitize_evidence(e) for e in extra]
    except Exception as exc:  # noqa: BLE001
        logger.debug("stream connector enrich skipped: %s", exc)

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
    # Phase 4 — Text2SQL hybrid routing (same hook as /api/chat): prepend
    # deterministic SQL rows. Fail-open, never blocks the stream.
    sql_block, sql_prov = "", {"data_sources": ["kb_evidence"]}
    try:
        from ..services.text2sql import structured_data_block

        sql_block, sql_prov = structured_data_block(
            retrieval_query, settings=settings, project_id=req.project_id
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("stream text2sql hook skipped: %s", exc)
    if sql_block:
        prompt = f"{sql_block}\n\n{prompt}"
    data_sources = list(sql_prov.get("data_sources") or ["kb_evidence"])
    try:
        from ..services.evidence_synthesis import enrich_chat_prompt

        if getattr(settings, "chat_skills_runtime_enabled", True):
            prompt = enrich_chat_prompt(
                prompt,
                mode=req.mode,
                skill_ids=list(req.selected_skills or []),
                mcp_server_ids=list(req.selected_mcp_servers or []),
                settings=settings,
                project_id=req.project_id,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("evidence prompt enrich skipped: %s", exc)
    return {
        "question": question,
        "prompt": prompt,
        "sources": sources,
        "kb_used": kb_used,
        "entity_resolution": entity_resolution,
        "kg_stats": kg_stats,
        "clarification": clarification,
        "rewritten_query": rewritten_query,
        "mode": req.mode,
        "selected_skills": list(req.selected_skills or []),
        "selected_mcp_servers": list(req.selected_mcp_servers or []),
        "data_sources": data_sources,
    }


def _sse(obj: dict) -> str:
    import json

    return f"data: {json.dumps(obj, ensure_ascii=False, default=str)}\n\n"


def _fire_auto_review(
    *,
    question: str,
    answer: str,
    citations: list,
    settings,
    session_id: str | None,
    project_id: str | None,
) -> None:
    """W2-8 (P1-12): turn-stop 自动审计，后台线程触发，不阻塞响应。

    maybe_auto_review 内部做开关/幂等/防抖/修正轮抑制，这里只负责不阻塞。
    """
    try:
        if not bool(getattr(settings, "auto_audit_enabled", False)):
            return
        import threading as _th
        import uuid as _uuid

        from ..services.reviewer_fix_loop import maybe_auto_review

        kwargs = dict(
            turn_id=_uuid.uuid4().hex,
            question=question,
            answer=answer,
            citations=citations,
            settings=settings,
            session_id=session_id,
            project_id=project_id,
        )
        _th.Thread(target=maybe_auto_review, kwargs=kwargs, daemon=True).start()
    except Exception as exc:  # noqa: BLE001
        logger.debug("auto review skipped: %s", exc)


def _finalize_evidence_fields(
    question: str,
    answer: str,
    citations: list,
    *,
    settings,
    mode: str | None,
    selected_skills: list[str] | None,
    project_id: str | None = None,
    sources: list | None = None,
    domain: str | None = None,
    history: list | None = None,
    structure: dict | None = None,
) -> tuple[str, dict | None, dict | None, dict | None, list | None]:
    """DOI annotate + optional reviewer + 1-round fix-loop.

    Returns (answer, doi_results, reviewer, reviewer_fix, citation_expand).
    """
    from ..services.evidence_synthesis import evidence_mode_active, postprocess_evidence_answer

    doi_results = None
    citation_expand = None
    reviewer = None
    reviewer_fix = None
    if evidence_mode_active(mode, settings) or selected_skills:
        answer, emeta = postprocess_evidence_answer(answer, settings=settings)
        doi_results = emeta.get("doi_results")
        citation_expand = emeta.get("citation_expand") or None
        reviewer = _run_evidence_review(
            question, answer, _claims_evidence(citations), settings
        )
        if (
            reviewer
            and not _reviewer_failed(reviewer)
            and (reviewer.get("status") or "pass") != "pass"
        ):
            try:
                from ..services.reviewer_fix_loop import run_fix_loop

                def _repair(_q: str, auditor: str) -> str:
                    repaired, _ = answer_question(
                        f"{question}\n\n{auditor}\n\n请输出修订后的完整回答：",
                        list(sources or citations),
                        domain=domain,
                        history=history,
                        structure=structure,
                    )
                    return _ensure_answer(repaired)

                answer, reviewer_fix = run_fix_loop(
                    question=question,
                    answer=answer,
                    citations=_claims_evidence(citations),
                    review=reviewer,
                    settings=settings,
                    repair_fn=_repair,
                    max_rounds=1,  # stream: at most 1 round
                    project_id=project_id,
                )
                if reviewer_fix and reviewer_fix.get("findings"):
                    reviewer = reviewer_fix["findings"]
            except Exception as exc:  # noqa: BLE001
                logger.debug("stream fix-loop skipped: %s", exc)
    return answer, doi_results, reviewer, reviewer_fix, citation_expand


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
        _openai_compatible_stream,
        effective_setting as _es,
        _resolve_openai_base_url,
    )

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
                    "mode": plan.get("mode") or req.mode,
                }
            )
        except Exception as exc:
            logger.warning("chat/stream 准备失败: %s", exc)
            yield _sse({"type": "error", "message": f"检索失败: {str(exc)[:200]}"})
            return

        # Evidence mode: try PaperQA async before token stream.
        try:
            from ..services.evidence_synthesis import (
                evidence_mode_active,
                try_paperqa_answer_async,
            )

            if evidence_mode_active(req.mode, settings):
                pq = await try_paperqa_answer_async(question, sources)
                if pq:
                    answer, citations = pq
                    answer = _ensure_answer(answer)
                    yield _sse({"type": "phase", "phase": "answering"})
                    yield _sse({"type": "token", "delta": answer})
                    yield _sse({"type": "phase", "phase": "claims"})
                    answer, doi_results, reviewer, reviewer_fix, citation_expand = (
                        _finalize_evidence_fields(
                            question,
                            answer,
                            citations,
                            settings=settings,
                            mode=req.mode,
                            selected_skills=list(req.selected_skills or []),
                            project_id=req.project_id,
                            sources=list(sources),
                            domain=req.domain,
                            history=list(req.history or []),
                            structure=req.structure,
                        )
                    )
                    claims, sources_audit = None, None
                    try:
                        claims, sources_audit = await asyncio.to_thread(
                            _claims_and_audit,
                            question,
                            answer,
                            citations,
                            settings=settings,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("chat/stream paperqa claims: %s", exc)
                    evidence_provenance = None
                    try:
                        from ..services.scholar_helpers import build_evidence_provenance

                        evidence_provenance = build_evidence_provenance(
                            doi_results=doi_results,
                            sourced_claims=claims,
                            enabled=bool(
                                getattr(settings, "evidence_provenance_enabled", True)
                            ),
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    yield _sse(
                        {
                            "type": "done",
                            "answer": answer,
                            "citations": [_sanitize_evidence(c) for c in citations],
                            "rag_backend": "paperqa",
                            "kb_chunks_used": kb_used,
                            "data_sources": plan.get("data_sources"),
                            "entity_resolution": plan["entity_resolution"],
                            "kg_retrieval_stats": plan["kg_stats"],
                            "clarification": plan["clarification"],
                            "rewritten_query": plan["rewritten_query"],
                            "sourced_claims": claims,
                            "sources_audit": sources_audit,
                            "mode": req.mode,
                            "doi_results": doi_results,
                            "citation_expand": citation_expand,
                            "evidence_provenance": evidence_provenance,
                            "evidence_reviewer": reviewer,
                            "reviewer_fix": reviewer_fix,
                        }
                    )
                    return
        except Exception as exc:  # noqa: BLE001
            logger.debug("paperqa stream path skipped: %s", exc)

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
                        "data_sources": plan.get("data_sources"),
                        "structured": structured,
                        "clarification": plan["clarification"],
                        "rewritten_query": plan["rewritten_query"],
                    }
                )
                return

            # markdown 主回答 — 可选 chem tool loop，否则 token 流。
            from ..services.chat_chem_tools import (
                build_chat_messages,
                build_tool_context,
                openai_tool_schemas,
                run_tool_loop_events,
                should_enable_tools,
            )

            use_tools = should_enable_tools(provider, settings)
            ctx = build_tool_context(
                structure=req.structure,
                attachment_source_ids=list(req.attachment_source_ids or []),
                settings=settings,
            )
            tools_used: list[str] = []

            if use_tools and openai_tool_schemas(ctx):
                yield _sse({"type": "phase", "phase": "tools"})
                loop = asyncio.get_running_loop()
                queue: asyncio.Queue = asyncio.Queue(maxsize=256)
                result_holder: dict = {}

                def tools_worker() -> None:
                    try:
                        base_url = _resolve_openai_base_url(
                            provider, _es(settings, "llm_base_url")
                        )
                        messages = build_chat_messages(prompt=prompt, ctx=ctx)
                        answer_acc = ""
                        used: list[str] = []
                        for ev in run_tool_loop_events(
                            messages=messages,
                            ctx=ctx,
                            api_key=api_key,
                            model=_es(settings, "llm_model"),
                            base_url=base_url,
                            max_tokens=2048,
                            settings=settings,
                        ):
                            if ev["type"] == "token":
                                answer_acc += ev.get("delta") or ""
                            if ev["type"] == "loop_done":
                                answer_acc = ev.get("answer") or answer_acc
                                used = list(ev.get("tools_used") or [])
                            try:
                                loop.call_soon_threadsafe(queue.put_nowait, ("ev", ev))
                            except RuntimeError:
                                return
                        result_holder["text"] = answer_acc
                        result_holder["tools_used"] = used
                    except Exception as exc:  # noqa: BLE001
                        result_holder["error"] = str(exc)[:300]
                    finally:
                        try:
                            loop.call_soon_threadsafe(queue.put_nowait, ("end", None))
                        except RuntimeError:
                            pass

                t = threading.Thread(target=tools_worker, daemon=True)
                t.start()
                parts: list[str] = []
                try:
                    while True:
                        kind, payload = await asyncio.wait_for(queue.get(), timeout=240)
                        if kind == "ev":
                            ev = payload
                            if ev["type"] in ("phase", "tool_start", "tool_result"):
                                yield _sse(ev)
                            elif ev["type"] == "token":
                                parts.append(ev.get("delta") or "")
                                yield _sse({"type": "token", "delta": ev.get("delta") or ""})
                            elif ev["type"] == "loop_done":
                                tools_used = list(ev.get("tools_used") or [])
                        else:
                            break
                except asyncio.TimeoutError:
                    yield _sse(
                        {
                            "type": "error",
                            "message": "回答超时(含化学工具), 请重试",
                        }
                    )
                    return
                finally:
                    t.join(timeout=0.2)

                if "error" in result_holder:
                    logger.warning(
                        "chat/stream chem tools failed, fallback direct: %s",
                        result_holder["error"],
                    )
                    # fall through to legacy stream below by not returning —
                    # only if we got no answer yet
                    if not (result_holder.get("text") or "".join(parts).strip()):
                        use_tools = False
                    else:
                        answer = (result_holder.get("text") or "".join(parts)).strip()
                        yield _sse({"type": "phase", "phase": "claims"})
                        cites = [
                            _sanitize_evidence(c)
                            for c in plan["sources"][: min(8, len(plan["sources"]))]
                        ]
                        claims, sources_audit = None, None
                        try:
                            claims, sources_audit = await asyncio.to_thread(
                                _claims_and_audit,
                                question,
                                answer,
                                cites,
                                settings=settings,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("chat/stream claims 失败: %s", exc)
                        done_payload = {
                            "type": "done",
                            "answer": answer,
                            "citations": cites,
                            "rag_backend": active_rag_backend(),
                            "kb_chunks_used": kb_used,
                            "data_sources": plan.get("data_sources"),
                            "entity_resolution": plan["entity_resolution"],
                            "kg_retrieval_stats": plan["kg_stats"],
                            "clarification": plan["clarification"],
                            "rewritten_query": plan["rewritten_query"],
                            "sourced_claims": claims,
                            "sources_audit": sources_audit,
                            "tools_used": tools_used or result_holder.get("tools_used") or [],
                        }
                        yield _sse(done_payload)
                        return
                else:
                    answer = (result_holder.get("text") or "".join(parts)).strip()
                    tools_used = result_holder.get("tools_used") or tools_used
                    yield _sse({"type": "phase", "phase": "claims"})
                    cites = [
                        _sanitize_evidence(c)
                        for c in plan["sources"][: min(8, len(plan["sources"]))]
                    ]
                    claims, sources_audit = None, None
                    try:
                        claims, sources_audit = await asyncio.to_thread(
                            _claims_and_audit,
                            question,
                            answer,
                            cites,
                            settings=settings,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("chat/stream claims 失败: %s", exc)
                    yield _sse(
                        {
                            "type": "done",
                            "answer": answer,
                            "citations": cites,
                            "rag_backend": active_rag_backend(),
                            "kb_chunks_used": kb_used,
                            "data_sources": plan.get("data_sources"),
                            "entity_resolution": plan["entity_resolution"],
                            "kg_retrieval_stats": plan["kg_stats"],
                            "clarification": plan["clarification"],
                            "rewritten_query": plan["rewritten_query"],
                            "sourced_claims": claims,
                            "sources_audit": sources_audit,
                            "tools_used": tools_used,
                        }
                    )
                    return

            # markdown 主回答 — token 流（无 tools 或 tools 失败回退）。
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

            citations = [
                _sanitize_evidence(c) for c in plan["sources"][: min(8, len(plan["sources"]))]
            ]
            answer, doi_results, reviewer, reviewer_fix, citation_expand = (
                _finalize_evidence_fields(
                    question,
                    answer,
                    citations,
                    settings=settings,
                    mode=req.mode,
                    selected_skills=list(req.selected_skills or []),
                    project_id=req.project_id,
                    sources=list(plan["sources"]),
                    domain=req.domain,
                    history=list(req.history or []),
                    structure=req.structure,
                )
            )

            # claims 收尾(12s 硬超时 → offline 降级)。
            yield _sse({"type": "phase", "phase": "claims"})
            claims, sources_audit = None, None
            try:
                claims, sources_audit = await asyncio.to_thread(
                    _claims_and_audit,
                    question,
                    answer,
                    citations,
                    settings=settings,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("chat/stream claims 失败: %s", exc)

            evidence_provenance = None
            try:
                from ..services.scholar_helpers import build_evidence_provenance

                evidence_provenance = build_evidence_provenance(
                    doi_results=doi_results,
                    sourced_claims=claims,
                    enabled=bool(getattr(settings, "evidence_provenance_enabled", True)),
                )
            except Exception:  # noqa: BLE001
                pass

            yield _sse(
                {
                    "type": "done",
                    "answer": answer,
                    "citations": citations,
                    "rag_backend": active_rag_backend(),
                    "kb_chunks_used": kb_used,
                    "data_sources": plan.get("data_sources"),
                    "entity_resolution": plan["entity_resolution"],
                    "kg_retrieval_stats": plan["kg_stats"],
                    "clarification": plan["clarification"],
                    "rewritten_query": plan["rewritten_query"],
                    "sourced_claims": claims,
                    "sources_audit": sources_audit,
                    "mode": req.mode,
                    "doi_results": doi_results,
                    "citation_expand": citation_expand,
                    "evidence_provenance": evidence_provenance,
                    "evidence_reviewer": reviewer,
                    "reviewer_fix": reviewer_fix,
                }
            )
            # W2-8 (P1-12): turn-stop 自动审计；evidence 路径已内联 review 的 turn 跳过。
            if reviewer is None:
                _fire_auto_review(
                    question=question,
                    answer=answer,
                    citations=_claims_evidence(citations),
                    settings=settings,
                    session_id=req.chat_session_id,
                    project_id=req.project_id,
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

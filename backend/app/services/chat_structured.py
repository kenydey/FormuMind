"""Structured chat answers via complete_structured."""
from __future__ import annotations

import logging
import re

from ..config import Settings, get_settings
from ..domain.chat_schemas import ChatTurn, StructuredAnswer, StructuredAnswerResponse
from ..domain.schemas import Evidence
from .errors import degrade_return
from .llm import complete_structured

logger = logging.getLogger(__name__)

# v8: _REF_RE 已删除（v7 删掉了唯一使用它的 elif 分支）。


def generate_structured_answer(
    question: str,
    sources: list[Evidence],
    *,
    history: list[ChatTurn] | None = None,
    domain: str | None = None,
    settings: Settings | None = None,
) -> tuple[StructuredAnswer | None, str | None, list[Evidence]]:
    """结构化问答。

    返回 ``(answer, err, effective_sources)`` —— ``effective_sources`` 是
    U-3 压缩后实际喂给 LLM 的 evidence 列表（P1-11：调用方取 citations
    必须用它，否则 ``[n]`` 与 LLM 所见坐标错位）。
    """
    settings = settings or get_settings()
    if not settings.chat_structured_enabled:
        return None, "structured chat disabled", sources

    # U-3: 结构化路径同样做 query-aware 证据压缩（只改 snippet 文本，
    # identifier/title 不动，evidence_ref 校验不受影响）。fail-open。
    if sources:
        try:
            from .query_aware_compression import (
                compress_evidence,
                query_compress_enabled,
                query_compress_token_budget,
            )

            if query_compress_enabled(settings):
                sources = compress_evidence(
                    question,
                    sources,
                    token_budget=query_compress_token_budget(settings),
                )
        except Exception as exc:  # noqa: BLE001 - fail-open
            logger.debug("structured query compression skipped: %s", exc)

    if not sources:
        fallback = StructuredAnswer(
            summary="暂无可用资料支撑结构化回答，请先检索或上传文献。",
            uncertainty_notes=["无 grounding sources"],
        )
        return fallback, None, sources

    system = (
        "你是配方化学问答助手。仅根据给定证据生成结构化 JSON。"
        "每条 formulation_hints.evidence_ref 必须是 citations 中的 identifier（如 kb:xxx#c0）"
        "或引用序号 [1]、[2]。不得编造证据。"
    )
    ev_lines = "\n".join(
        # v7 问答-5: 统一为 8 —— citations 只取 eff_sources[:8]，
        # prompt 给 12 会导致 LLM 引用 [9]-[12] 时前端无卡片。
        f"[{i+1}] id={e.identifier} ({e.source}) {e.title}: {e.snippet[:350]}"
        for i, e in enumerate(sources[:8])
    )
    hist_lines = ""
    if history:
        hist_lines = "\n".join(
            f"{t.role}: {t.content[:300]}" for t in history[-4:]
        )
    domain_hint = f"Domain: {domain}\n" if domain else ""
    user = (
        f"{domain_hint}"
        f"对话历史:\n{hist_lines or '(无)'}\n\n"
        f"证据:\n{ev_lines}\n\n"
        f"问题: {question}\n\n"
        "返回 StructuredAnswer：summary、key_findings、formulation_hints、"
        "data_conflicts、uncertainty_notes、assumptions。"
    )

    try:
        parsed, err = complete_structured(system, user, StructuredAnswerResponse)
        if parsed is None or not parsed.answer.summary.strip():
            return None, err or "structured parse failed", sources
        # v8: sanitize 用 sources[:8] —— prompt 只展示前 8 条，[9]+ 无卡片，
        # 必须同步拦掉，否则引用悬空。
        cleaned = _sanitize_structured(parsed.answer, sources[:8])
        return cleaned, None, sources
    except Exception as exc:
        return degrade_return(logger, exc, "structured chat failed", None), str(exc), sources


def _sanitize_structured(answer: StructuredAnswer, sources: list[Evidence]) -> StructuredAnswer:
    valid_ids = {e.identifier for e in sources if e.identifier}
    valid_refs = valid_ids | {f"[{i+1}]" for i in range(len(sources))}
    hints = []
    for hint in answer.formulation_hints:
        ref = (hint.evidence_ref or "").strip()
        # v7 问答-3: 删除 elif 分支 —— valid_refs 已覆盖全部合法 [1..n]，
        # 越界引用（如 [99]）必须丢弃，不能放行。
        if ref in valid_refs:
            hints.append(hint)
    return answer.model_copy(update={"formulation_hints": hints})

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

_REF_RE = re.compile(r"^\[\d+\]$")


def generate_structured_answer(
    question: str,
    sources: list[Evidence],
    *,
    history: list[ChatTurn] | None = None,
    domain: str | None = None,
    settings: Settings | None = None,
) -> tuple[StructuredAnswer | None, str | None]:
    settings = settings or get_settings()
    if not settings.chat_structured_enabled:
        return None, "structured chat disabled"

    if not sources:
        fallback = StructuredAnswer(
            summary="暂无可用资料支撑结构化回答，请先检索或上传文献。",
            uncertainty_notes=["无 grounding sources"],
        )
        return fallback, None

    system = (
        "你是配方化学问答助手。仅根据给定证据生成结构化 JSON。"
        "每条 formulation_hints.evidence_ref 必须是 citations 中的 identifier（如 kb:xxx#c0）"
        "或引用序号 [1]、[2]。不得编造证据。"
    )
    ev_lines = "\n".join(
        f"[{i+1}] id={e.identifier} ({e.source}) {e.title}: {e.snippet[:350]}"
        for i, e in enumerate(sources[:12])
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
            return None, err or "structured parse failed"
        cleaned = _sanitize_structured(parsed.answer, sources)
        return cleaned, None
    except Exception as exc:
        return degrade_return(logger, exc, "structured chat failed", None), str(exc)


def _sanitize_structured(answer: StructuredAnswer, sources: list[Evidence]) -> StructuredAnswer:
    valid_ids = {e.identifier for e in sources if e.identifier}
    valid_refs = valid_ids | {f"[{i+1}]" for i in range(len(sources))}
    hints = []
    for hint in answer.formulation_hints:
        ref = (hint.evidence_ref or "").strip()
        if ref in valid_refs or ref in valid_ids:
            hints.append(hint)
        elif _REF_RE.match(ref):
            hints.append(hint)
    return answer.model_copy(update={"formulation_hints": hints})

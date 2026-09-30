"""Multi-turn chat context — query rewrite from client history."""
from __future__ import annotations

import logging
import re

from ..config import Settings, get_settings
from ..domain.chat_schemas import ChatTurn, ClarifiedEntity
from .errors import degrade_return

logger = logging.getLogger(__name__)

_CAS_RE = re.compile(r"\b(\d{2,7}-\d{2}-\d)\b")
_FOLLOWUP_MARKERS = re.compile(
    r"(它|其|该|此|这个|那个|上面|上述|前者|后者|怎么样|如何|多少|呢|吗)",
    re.IGNORECASE,
)
_CHEM_TOKEN_RE = re.compile(
    r"(磷酸锌|环氧树脂|固化剂|防锈颜料|乳液|牌号|盐雾|添加量|wt%|实施例|[A-Za-z]{2,}[- ]?\d{2,4})"
)


def _summarize_turns(turns: list[ChatTurn]) -> ChatTurn:
    """Deterministic extractive summary of dropped older turns (B-2).

    Reuses the query-rewrite term machinery (CAS / chem terms / citation
    titles) so the entities that matter for retrieval survive compression,
    plus the first user question for intent. No LLM call — deterministic and
    cheap, so long conversations don't pay a summary call per turn.
    """
    terms = _collect_context_terms(turns, [])
    first_q = next(
        (
            (t.content or "").strip()
            for t in turns
            if t.role == "user" and (t.content or "").strip()
        ),
        "",
    )
    parts = [f"此前 {len(turns)} 轮对话"]
    if terms:
        parts.append("关键实体：" + "、".join(terms[:8]))
    if first_q:
        parts.append("首个问题：" + first_q[:120])
    return ChatTurn(role="assistant", content="[历史摘要] " + "；".join(parts))


def trim_history(
    history: list[ChatTurn],
    *,
    max_turns: int | None = None,
    token_budget: int | None = None,
) -> list[ChatTurn]:
    """Token-budget-aware history trim (B-2).

    Replaces the old blind N-turn hard cut: after the ``max_turns`` hard
    backstop, the newest turns that fit into the token budget are kept and
    the dropped older turns are folded into a single deterministic summary
    turn, so early key entities are never silently lost. Total prompt length
    stays bounded by the budget.
    """
    settings = get_settings()
    cap = max_turns if max_turns is not None else settings.chat_history_max_turns
    budget = (
        token_budget
        if token_budget is not None
        else settings.chat_history_token_budget
    )
    turns = list(history or [])
    if len(turns) > cap:
        turns = turns[-cap:]
    if not turns:
        return turns

    from .query_aware_compression import estimate_tokens

    def _tokens(t: ChatTurn) -> int:
        return estimate_tokens(t.content or "")

    if not budget or budget <= 0 or sum(_tokens(t) for t in turns) <= budget:
        return turns

    # 从最新往最旧累积：保留能装进预算的轮次，被挤掉的旧轮次压缩为摘要。
    # reserve 给摘要预留 token；最新一轮永远保留（prompt 层另有单轮截断）。
    reserve = min(400, max(100, budget // 4))
    kept: list[ChatTurn] = []
    used = 0
    idx = len(turns)
    for t in reversed(turns):
        cost = _tokens(t)
        if kept and used + cost > budget - reserve:
            break
        kept.append(t)
        used += cost
        idx -= 1
    kept.reverse()
    dropped = turns[:idx]
    if not dropped:
        return kept
    summary = _summarize_turns(dropped)
    # 摘要本身约束在预留内（estimate_tokens 按 chars/4 估算）。
    max_chars = reserve * 4
    if len(summary.content) > max_chars:
        summary = ChatTurn(
            role="assistant", content=summary.content[: max_chars - 1] + "…"
        )
    return [summary] + kept


def rewrite_query(
    question: str,
    history: list[ChatTurn] | None,
    clarified_entities: list[ClarifiedEntity] | None = None,
    *,
    settings: Settings | None = None,
) -> tuple[str, str | None]:
    """Return (query_for_retrieval, rewritten_query_or_none)."""
    settings = settings or get_settings()
    q = (question or "").strip()
    if not q or not settings.chat_multi_turn_enabled:
        return q, None

    history = trim_history(history or [], max_turns=settings.chat_history_max_turns)
    if not history:
        return q, None

    try:
        context_turns = history[-settings.chat_rewrite_context_turns :]
        terms = _collect_context_terms(context_turns, clarified_entities or [])
        if not terms:
            return q, None

        needs_context = bool(_FOLLOWUP_MARKERS.search(q)) or len(q) <= 24
        if not needs_context:
            return q, None

        rewritten = f"{' '.join(terms)} {q}".strip()
        if rewritten == q:
            return q, None
        return rewritten, rewritten
    except Exception as exc:
        degrade_return(logger, exc, "chat query rewrite failed", None)
        return q, None


def _collect_context_terms(
    turns: list[ChatTurn],
    clarified: list[ClarifiedEntity],
) -> list[str]:
    terms: list[str] = []
    for ce in clarified:
        if ce.resolved:
            terms.append(ce.resolved.strip())
        elif ce.term:
            terms.append(ce.term.strip())

    for turn in reversed(turns):
        text = (turn.content or "").strip()
        if not text:
            continue
        for cas in _CAS_RE.findall(text):
            terms.append(cas)
        for tok in _CHEM_TOKEN_RE.findall(text):
            if tok and tok not in terms:
                terms.append(tok)
        for ev in turn.citations or []:
            title = (ev.title or "").split("·")[0].strip()
            if title and len(title) >= 2 and title not in terms:
                terms.append(title[:48])
        if len(terms) >= 8:
            break

    return list(dict.fromkeys(t for t in terms if t))[:8]

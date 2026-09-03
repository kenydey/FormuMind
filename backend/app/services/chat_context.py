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


def trim_history(history: list[ChatTurn], *, max_turns: int | None = None) -> list[ChatTurn]:
    settings = get_settings()
    cap = max_turns if max_turns is not None else settings.chat_history_max_turns
    if len(history) <= cap:
        return history
    return history[-cap:]


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

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

# P2: 可消解的代词 —— 排除"应该"（应+该）、"尤其"（尤+其）、
# "其他/其它/其余/其中"（其+他/它/余/中，非指代）。
# v7: 补"其实/其次/其后"（其+实/次/后）、"极其"（极+其）、
# "此时/此前/此后/此次"（此+时/前/后/次）、"这个时候"。
# v8: 补"其它"（其+它已排除，但独立的"它"分支会误匹配第二个字）、
# "因此"/"在此"（高频连词/介词，非指代）。
_ANAPHORA_RE = re.compile(
    r"(?<!应)该|(?<!尤)(?<!极)其(?!他|它|余|中|实|次|后)|(?<!其)它|(?<!因)(?<!在)此(?!时|前|后|次|致)|这个(?!时候)|那个|上述|上面|前者|后者"
)
# 做先行词时过滤的泛词（是检索词条，但不是可指代的实体）。
_ANAPHORA_STOPWORDS = frozenset({"实施例", "wt%", "添加量", "盐雾", "牌号"})


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

        # P2: 真正的指代消解 —— 代词替换成先行词，而不仅是前置词条。
        resolved_q = _resolve_anaphora(q, context_turns, clarified_entities or [])
        rewritten = f"{' '.join(terms)} {resolved_q}".strip()
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


def _antecedents(
    turns: list[ChatTurn],
    clarified: list[ClarifiedEntity],
) -> list[str]:
    """P2: 按新鲜度排序的先行词候选（澄清实体 > CAS > 化学实体 > 文献标题）。

    与 _collect_context_terms 不同：过滤掉"实施例/wt%/添加量"这类
    泛词——它们是检索词条，但不能做代词的先行词。
    """
    cands: list[str] = []
    for ce in clarified:
        resolved = (ce.resolved or ce.term or "").strip()
        if resolved:
            cands.append(resolved)
    for turn in reversed(turns):
        text = (turn.content or "").strip()
        if not text:
            continue
        # 轮内按提及位置倒序 —— 代词指最近提及的实体。
        for cas in reversed(_CAS_RE.findall(text)):
            cands.append(cas)
        for tok in reversed(_CHEM_TOKEN_RE.findall(text)):
            if tok and tok not in _ANAPHORA_STOPWORDS:
                cands.append(tok)
        cits = []
        for ev in turn.citations or []:
            title = (ev.title or "").split("·")[0].strip()
            if title and len(title) >= 2:
                cits.append(title[:48])
        cands.extend(reversed(cits))
    # 去重保序（最新优先）。
    return list(dict.fromkeys(c for c in cands if c))


def _resolve_anaphora(
    question: str,
    turns: list[ChatTurn],
    clarified: list[ClarifiedEntity],
) -> str:
    """P2: 代词消解 —— 把"它/该/这个/那个…"替换成历史最近的关键实体。

    只替换首个可消解代词；无候选先行词时原文返回。Fail-open：异常时
    返回原问句。
    """
    try:
        antecedents = _antecedents(turns, clarified)
        if not antecedents:
            return question

        def _sub(m: "re.Match[str]") -> str:
            pron = m.group(0)
            # 前者=较早提及的实体，后者=最近的（新鲜度倒序）。
            if pron == "前者":
                return antecedents[1] if len(antecedents) > 1 else pron
            if pron == "后者":
                return antecedents[0]
            return antecedents[0]

        return _ANAPHORA_RE.sub(_sub, question, count=1)
    except Exception:  # noqa: BLE001 - fail-open
        return question

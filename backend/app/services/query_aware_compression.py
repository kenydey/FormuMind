"""W5-1 (P2-1): query-aware evidence compression — tier 1 (cheap, no LLM)
plus tier 2 (LLM rewrite).

``compress_evidence`` is a pure function: it re-ranks a list of ``Evidence``
by query relevance, de-duplicates passages from the same source, and fits the
result into a token budget with per-item degradation
(full passage -> short snippet -> metadata only).

``llm_compress_evidence`` (tier 2) rewrites long passages into query-focused
summaries with a small/cheap LLM. Only long texts trigger an LLM call;
short texts pass through untouched. Citation anchors (identifier, title,
page/paragraph, url, source) are preserved — only the ``snippet`` is
replaced, visibly marked with ``LLM_SUMMARY_PREFIX``.

Fail-open: any internal exception returns the tier-1 ``compress_evidence``
result (double safety net) and never mutates the caller's items.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from typing import Any

from ..domain.schemas import Evidence
from .search_scoring import age_normalized_citation_score

logger = logging.getLogger(__name__)

# --- Wave 3-2: lightweight in-process observability ---------------------------
# Process-local cumulative counters for tier-2 (LLM) evidence compression.
# No new storage: served via GET /api/ops/evidence-stats. Counters only move
# forward; a process restart resets them (documented on the endpoint).
_EVIDENCE_STATS: dict[str, int] = {
    "tier2_attempts": 0,  # llm_compress_evidence entry (flag was on at call site)
    "tier2_triggers": 0,  # long items present -> exactly one batched LLM call
    "llm_calls": 0,  # == tier2_triggers by construction (one call per trigger)
    "tokens_before": 0,  # estimated tokens of long snippets sent for rewrite
    "tokens_after": 0,  # estimated tokens of the returned summaries
}
_STATS_LOCK = threading.Lock()


def get_evidence_stats() -> dict[str, int]:
    """Return a snapshot copy of the tier-2 compression counters."""
    with _STATS_LOCK:
        return dict(_EVIDENCE_STATS)


def reset_evidence_stats() -> None:
    """Zero all counters. Tests only — never called in production paths."""
    with _STATS_LOCK:
        for key in _EVIDENCE_STATS:
            _EVIDENCE_STATS[key] = 0


def _bump_stats(**deltas: int) -> None:
    with _STATS_LOCK:
        for key, delta in deltas.items():
            _EVIDENCE_STATS[key] = _EVIDENCE_STATS.get(key, 0) + delta

# Default budget when the caller does not pass one (also the settings
# fallback for ``query_compress_token_budget``).
DEFAULT_TOKEN_BUDGET = 12000
# Max evidence items kept per source identifier (anti single-paper flooding).
MAX_PER_SOURCE = 3
# Char length of the degraded "short snippet" form.
SHORT_SNIPPET_CHARS = 400

# Rough char->token estimate for latin/CJK mixed text (no tokenizer dep).
_CHARS_PER_TOKEN = 4

_STOPWORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
        "is", "are", "was", "were", "be", "by", "as", "at", "from", "that",
        "this", "it", "its", "what", "which", "how", "why", "when", "who",
        "do", "does", "about", "into", "over", "under", "between", "during",
        "的", "了", "和", "与", "或", "在", "是", "有", "对", "及", "等",
    }
)

_TERM_RE = re.compile(r"[\w]+", re.UNICODE)


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (chars / 4, min 1)."""
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def query_terms(question: str) -> list[str]:
    """Split a question into searchable terms (latin + CJK aware)."""
    terms: list[str] = []
    for tok in _TERM_RE.findall((question or "").lower()):
        if len(tok) < 2:
            continue
        if tok in _STOPWORDS:
            continue
        if tok not in terms:
            terms.append(tok)
    return terms


def term_coverage(terms: list[str], text: str) -> float:
    """Fraction of query terms present (substring) in the text."""
    if not terms or not text:
        return 0.0
    hay = text.lower()
    hits = sum(1 for t in terms if t in hay)
    return hits / len(terms)


def evidence_score(question_terms: list[str], ev: Evidence) -> float:
    """Query-biased score in [0, 1].

    0.55 query-term coverage + 0.30 retrieval relevance + 0.15
    age-normalized citation signal (W4-6, reuses
    ``age_normalized_citation_score`` so new papers are not drowned).
    """
    text = f"{ev.title or ''} {ev.snippet or ''}"
    coverage = term_coverage(question_terms, text)
    try:
        relevance = float(ev.relevance or 0.0)
    except (TypeError, ValueError):
        relevance = 0.0
    relevance = max(0.0, min(1.0, relevance))
    try:
        age01 = max(0.0, min(1.0, age_normalized_citation_score(ev) / 0.12))
    except Exception:  # noqa: BLE001 - scoring must never raise
        age01 = 0.0
    return 0.55 * coverage + 0.30 * relevance + 0.15 * age01


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _dedup_source(
    items: list[Evidence],
    scores: dict[int, float],
) -> list[Evidence]:
    """Drop near-duplicate passages within one source, cap count.

    Keeps the highest-scoring items; an item is dropped when its normalized
    text is a substring of an already-kept item's text (or vice versa, in
    which case the longer one wins).
    """
    kept: list[Evidence] = []
    for ev in sorted(items, key=lambda e: scores[id(e)], reverse=True):
        norm = _normalize(f"{ev.title} {ev.snippet}")
        dominated = False
        for i, prev in enumerate(kept):
            pnorm = _normalize(f"{prev.title} {prev.snippet}")
            if norm == pnorm or norm in pnorm:
                dominated = True
                break
            if pnorm in norm:
                # Current item strictly contains the kept one: replace it.
                kept[i] = ev
                dominated = True
                break
        if not dominated:
            kept.append(ev)
        if len(kept) >= MAX_PER_SOURCE:
            break
    return kept


def _full_text(ev: Evidence) -> str:
    return f"{ev.title or ''}. {ev.snippet or ''}".strip()


def _degraded(ev: Evidence, remaining: int) -> Evidence | None:
    """Best degraded form of ``ev`` that fits ``remaining`` tokens.

    full -> short snippet -> metadata only. Returns None when even the
    metadata-only form does not fit.
    """
    full = _full_text(ev)
    if estimate_tokens(full) <= remaining:
        return ev
    raw = (ev.snippet or "").rstrip()
    if len(raw) > SHORT_SNIPPET_CHARS:
        cand = ev.model_copy(update={"snippet": raw[:SHORT_SNIPPET_CHARS].rstrip() + "…"})
        if estimate_tokens(_full_text(cand)) <= remaining:
            return cand
    meta = (ev.title or "").strip()
    if meta and estimate_tokens(meta) <= remaining:
        return ev.model_copy(update={"snippet": ""})
    return None


def compress_evidence(
    question: str,
    sources: list[Evidence],
    *,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> list[Evidence]:
    """Re-rank, de-duplicate and budget-fit evidence for a question.

    Pure function: never mutates ``sources``; degraded items are returned
    as ``model_copy`` instances. Fail-open — any exception returns
    ``list(sources)`` unchanged.
    """
    try:
        return _compress_evidence(question, sources, token_budget=token_budget)
    except Exception as exc:  # noqa: BLE001 - fail-open contract
        logger.warning("query compression failed, returning original sources: %s", exc)
        return list(sources)


def _compress_evidence(
    question: str,
    sources: list[Evidence],
    *,
    token_budget: int,
) -> list[Evidence]:
    if not sources:
        return []
    if token_budget <= 0:
        # Degenerate budget: nothing can be fitted honestly — fail open.
        return list(sources)

    terms = query_terms(question)
    # Scores live outside the Evidence objects: never mutate the caller's items.
    scores: dict[int, float] = {id(ev): evidence_score(terms, ev) for ev in sources}

    # Same-source merge/dedup (anti flooding), then global re-rank.
    groups: dict[str, list[Evidence]] = {}
    for ev in sources:
        key = ev.identifier or ev.title or id(ev)
        groups.setdefault(key, []).append(ev)
    deduped: list[Evidence] = []
    for items in groups.values():
        deduped.extend(_dedup_source(items, scores))
    deduped.sort(key=lambda e: scores[id(e)], reverse=True)

    out: list[Evidence] = []
    remaining = token_budget
    for ev in deduped:
        picked = _degraded(ev, remaining)
        if picked is None:
            continue
        out.append(picked)
        remaining -= estimate_tokens(_full_text(picked))
        if remaining <= 0:
            break

    if not out:
        # Budget too small for even metadata: keep the single best item as
        # metadata-only rather than returning nothing (caller expects
        # evidence for synthesis).
        best = deduped[0]
        out.append(best.model_copy(update={"snippet": ""}))
    return out


# --- feature flag helpers (read at the call site, not inside the pure fn) ---


def query_compress_enabled(settings: Any) -> bool:
    """W5-1 kill-switch. Defaults ON (tier 1 is cheap and fail-open)."""
    return bool(getattr(settings, "query_compress_enabled", True))


def query_compress_token_budget(settings: Any) -> int:
    """Token budget for compression; falls back to the module default."""
    try:
        value = int(getattr(settings, "query_compress_token_budget", DEFAULT_TOKEN_BUDGET))
    except (TypeError, ValueError):
        return DEFAULT_TOKEN_BUDGET
    return value if value > 0 else DEFAULT_TOKEN_BUDGET


def query_compress_llm_enabled(settings: Any) -> bool:
    """W5-1 tier-2 kill-switch. Defaults ON (2026-09-28 Wave 1 ablation).

    Override with ``FORMUMIND_QUERY_COMPRESS_LLM_ENABLED=false``.
    """
    return bool(getattr(settings, "query_compress_llm_enabled", False))


# --- tier 2: LLM rewrite layer ---

# Only items with a snippet at least this long trigger an LLM call
# (cost control: short texts pass through untouched).
MIN_SNIPPET_CHARS_FOR_LLM = 1500
# Max long items summarized in a single LLM call (cost control; extra long
# items pass through untouched and are handled by the tier-1 budget fit).
MAX_LLM_SUMMARY_ITEMS = 6
# Max chars of one passage sent to the LLM (bounds prompt cost).
MAX_PASSAGE_CHARS_FOR_LLM = 8000
# Visible marker so downstream consumers know the snippet is an LLM summary,
# not the original text.
LLM_SUMMARY_PREFIX = "[LLM 压缩摘要] "

_LLM_SUMMARY_PROMPT = """你是证据压缩助手。用户问题如下，每条证据 passage 带有唯一 id。

任务：为每条 passage 写一段与该问题直接相关的摘要（中文，200 字以内）。
要求：
1. 只保留与问题相关的关键事实、数据、数值、结论；无关内容删掉。
2. 摘要中的数字、结论必须来自对应 passage 原文，严禁编造或引入外部知识。
3. 只输出 JSON，不要输出其他内容，格式如下：
{{"summaries": [{{"id": "<原 passage id>", "summary": "<摘要>"}}, ...]}}

问题：{question}

Passages:
{passages}
"""


def _reviewer_model(settings: Any) -> str | None:
    """Small/cheap model for compression (W5-2). Empty → inherit chat model."""
    raw = getattr(settings, "evidence_reviewer_model", "") or ""
    model = str(raw).strip()
    return model or None


def _build_llm_summary_prompt(question: str, items: list[Evidence]) -> str:
    blocks = []
    for ev in items:
        text = (ev.snippet or "")[:MAX_PASSAGE_CHARS_FOR_LLM]
        blocks.append(f"[id={ev.identifier}] {ev.title or ''}\n{text}")
    return _LLM_SUMMARY_PROMPT.format(
        question=(question or "").strip(),
        passages="\n\n".join(blocks),
    )


def _apply_summaries(
    sources: list[Evidence],
    summary_by_id: dict[str, str],
) -> list[Evidence]:
    """Replace snippets with summaries; keep every other field intact.

    Citation anchors (identifier, title, page/paragraph, url, source,
    cited_by, pub_year, ...) are never touched — only ``snippet`` is
    replaced, with a visible summary marker. Unknown ids from the LLM are
    ignored (never invent evidence); items without a summary pass through.
    """
    out: list[Evidence] = []
    for ev in sources:
        summary = summary_by_id.get(ev.identifier)
        if summary:
            out.append(
                ev.model_copy(
                    update={"snippet": LLM_SUMMARY_PREFIX + summary.strip()}
                )
            )
        else:
            out.append(ev)
    return out


def llm_compress_evidence(
    question: str,
    sources: list[Evidence],
    *,
    settings: Any,
) -> list[Evidence]:
    """Tier 2: rewrite long passages into query-focused summaries.

    Only items with ``len(snippet) >= MIN_SNIPPET_CHARS_FOR_LLM`` trigger
    the LLM (one batched call for up to ``MAX_LLM_SUMMARY_ITEMS`` items);
    everything else passes through untouched. Fail-open — any exception,
    an empty/invalid LLM reply, or zero usable summaries falls back to the
    tier-1 ``compress_evidence`` result.
    """
    # Wave 3-2: count every entry (the call site only calls us when the flag
    # is on). Bump before the try so even a fail-open fallback counts.
    _bump_stats(tier2_attempts=1)
    try:
        return _llm_compress_evidence(question, sources, settings=settings)
    except Exception as exc:  # noqa: BLE001 - fail-open contract
        logger.warning(
            "LLM evidence compression failed, falling back to tier 1: %s", exc
        )
        return compress_evidence(
            question, sources, token_budget=query_compress_token_budget(settings)
        )


def _llm_compress_evidence(
    question: str,
    sources: list[Evidence],
    *,
    settings: Any,
) -> list[Evidence]:
    if not sources:
        return []
    # Wave 3-2: log a query hash, never the raw question text.
    qhash = hashlib.sha256(question.encode("utf-8", "ignore")).hexdigest()[:12]
    long_items = [
        ev
        for ev in sources
        if len(ev.snippet or "") >= MIN_SNIPPET_CHARS_FOR_LLM
    ]
    if not long_items:
        # No long texts: nothing for the LLM to do — skip the call entirely.
        logger.info(
            "tier2 evidence compression: query=%s triggered=false reason=no_long_items",
            qhash,
        )
        return list(sources)
    batch = long_items[:MAX_LLM_SUMMARY_ITEMS]

    from . import llm as _llm

    prompt = _build_llm_summary_prompt(question, batch)
    data = _llm.complete_json(prompt, model=_reviewer_model(settings))
    if not isinstance(data, dict):
        raise ValueError("LLM compression returned no usable JSON")
    raw_summaries = data.get("summaries")
    if not isinstance(raw_summaries, list):
        raise ValueError("LLM compression JSON missing 'summaries' list")

    known_ids = {ev.identifier for ev in sources}
    summary_by_id: dict[str, str] = {}
    for item in raw_summaries:
        if not isinstance(item, dict):
            continue
        sid = item.get("id")
        text = item.get("summary")
        if (
            isinstance(sid, str)
            and sid in known_ids
            and sid not in summary_by_id
            and isinstance(text, str)
            and text.strip()
        ):
            summary_by_id[sid] = text.strip()
    if not summary_by_id:
        raise ValueError("LLM compression produced no usable summaries")
    # Wave 3-2: one batched LLM call per trigger; record token economics.
    tokens_before = sum(estimate_tokens(ev.snippet or "") for ev in batch)
    tokens_after = sum(estimate_tokens(s) for s in summary_by_id.values())
    _bump_stats(
        tier2_triggers=1,
        llm_calls=1,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
    )
    logger.info(
        "tier2 evidence compression: query=%s triggered=true items=%d/%d "
        "tokens_before=%d tokens_after=%d llm_calls=1",
        qhash,
        len(summary_by_id),
        len(batch),
        tokens_before,
        tokens_after,
    )
    logger.debug(
        "LLM evidence compression: %d/%d long items summarized",
        len(summary_by_id),
        len(batch),
    )
    return _apply_summaries(sources, summary_by_id)

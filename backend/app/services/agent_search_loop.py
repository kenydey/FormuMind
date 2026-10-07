"""Agentic iterative retrieval loop (Round 3, Wave 1).

Single-shot retrieval (``kb_index.retrieve_evidence``) issues one query and
stops. This module adds the PaperQA2-style loop on top:

    retrieve -> assess facet-coverage gap -> rewrite query -> retrieve ...

Only the *retrieval* stage is iterative; downstream rerank / compression /
synthesis are untouched. Everything is fail-open: a failed round keeps the
evidence gathered so far, and hard guards (max iterations, time budget) bound
cost. Gap assessment is a deterministic heuristic; an LLM assessor can be
injected via ``assess_fn`` (there is deliberately no config flag for it yet).
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from ..domain.schemas import Evidence
from .errors import degrade_return
from .rag import _bm25_tokenize

logger = logging.getLogger(__name__)

__all__ = [
    "AgentSearchResult",
    "agent_search",
    "agent_search_enabled",
    "assess_gap",
    "assess_gap_plus",
    "extract_facets",
    "extract_numeric_facets",
    "rewrite_query",
]

# Numeric completeness checkpoint (Phase 2): a query like "耐蚀性 > 72h" is not
# answered by evidence that merely mentions 耐蚀性 without the number. Numbers
# with units are extracted deterministically and must appear in the evidence.
_NUM_UNIT_RE = re.compile(  # (?<!\d): see numeric_check._NUM_RE — a digit run with no unit was retried from every digit
    r"(?<!\d)\d+(?:\.\d+)?\s*(?:g/L|mg/L|mol/L|°C|℃|%|wt%|wt\.%|ppm|ppb|MPa|kPa|Pa|"
    r"N·m|N|μm|um|mm|cm|mL|L|kg|g|h|小时|分钟|min|s|V|A|pH)",
    re.IGNORECASE,
)


def extract_numeric_facets(query: str) -> list[str]:
    """Numbers-with-units in the query that evidence must reproduce."""
    seen: list[str] = []
    for m in _NUM_UNIT_RE.finditer(unicodedata.normalize("NFKC", query or "")):
        t = _norm(m.group(0))
        if t and t not in seen:
            seen.append(t)
    return seen


# Tokens too short / too generic to be a facet. Kept tiny and deterministic.
_FACET_STOPWORDS = {
    "如何", "什么", "怎么", "为什么", "哪些", "多少", "可以", "能够",
    "进行", "使用", "用于", "通过", "这个", "一种",
}
# P1-1: 单字停用词。统一分词器产出二字重叠对（如 "的了"），若对子两字皆为
# 停用单字则整体丢弃，避免无意义 facet。
_FACET_STOP_CHARS = frozenset("的了是在有和与或不这那个一")


def _norm(text: str) -> str:
    # NFKC so "80 µm" (micro sign) in the evidence and "80 μm" (Greek mu) in the question are the same facet.
    t = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[\s\u3000\-–—_.,;:!?，。；：！？、（）()\[\]【】\"'“”‘’·/\\]+", "", t)


def agent_search_enabled(settings: Any | None = None) -> bool:
    """Kill-switch for the agentic loop. Default OFF until A/B decides."""
    if settings is None:
        from ..config import get_settings

        settings = get_settings()
    return bool(getattr(settings, "agent_search_enabled", False))


def _agent_cfg(settings: Any | None, name: str, default: Any) -> Any:
    if settings is None:
        from ..config import get_settings

        settings = get_settings()
    return getattr(settings, name, default)


def extract_facets(query: str, max_facets: int = 8) -> list[str]:
    """Split a question into facet terms (heuristic, deterministic).

    CJK-aware via the shared BM25 tokenizer; drops stopwords / 1-char noise.
    """
    seen: list[str] = []
    for tok in _bm25_tokenize(query or ""):
        t = tok.strip()
        if len(t) < 2 or t in _FACET_STOPWORDS:
            continue
        if all(ch in _FACET_STOP_CHARS for ch in t):
            continue
        # v16 P3-5: 二字 CJK 含停用单字多为切分碎片（如"的防"），丢弃。
        if len(t) == 2 and any(ch in _FACET_STOP_CHARS for ch in t):
            continue
        if t not in seen:
            seen.append(t)
        if len(seen) >= max_facets:
            break
    return seen


def _evidence_text(ev: Evidence) -> str:
    return _norm(ev.title + " " + ev.snippet)


def assess_gap(facets: list[str], evidence: list[Evidence]) -> list[str]:
    """Return facets NOT covered by any retrieved evidence (heuristic).

    A facet is covered when its normalized form appears in a title/snippet.
    """
    if not facets:
        return []
    covered: set[str] = set()
    texts = [_evidence_text(ev) for ev in evidence]
    for facet in facets:
        needle = _norm(facet)
        if not needle:
            continue
        if any(needle in t for t in texts):
            covered.add(facet)
    return [f for f in facets if f not in covered]


def assess_gap_plus(
    facets: list[str], evidence: list[Evidence], query: str = ""
) -> list[str]:
    """Facet coverage + numeric completeness (Phase 2, deterministic).

    Backward compatible with ``assess_gap``: everything ``assess_gap`` flags
    is still flagged. Additionally, numbers-with-units extracted from the
    query (e.g. "72h", "50g/L") must appear in the evidence text; a missing
    number is reported as ``"num:<value>"`` so the rewrite loop can target it.
    """
    uncovered = assess_gap(facets, evidence)
    if not query:
        return uncovered
    texts = [_evidence_text(ev) for ev in evidence]
    for num in extract_numeric_facets(query):
        if not any(num in t for t in texts):
            uncovered.append(f"num:{num}")
    return uncovered


def rewrite_query(original: str, uncovered_facets: list[str]) -> str:
    """Deterministic rewrite: original question + uncovered facet terms."""
    extra = " ".join(uncovered_facets).strip()
    if not extra:
        return original
    if extra in original:
        return original
    return original + " " + extra


def _evidence_key(ev: Evidence) -> str:
    return (
        _norm(ev.identifier or "")
        or _norm(ev.title or "")
        or _norm(ev.snippet[:64])
    )


@dataclass
class AgentSearchResult:
    evidence: list[Evidence]
    rounds: int
    queries_issued: list[str] = field(default_factory=list)
    facets: list[str] = field(default_factory=list)
    covered_facets: list[str] = field(default_factory=list)
    stopped_reason: str = ""  # covered|no_new|max_iters|budget|error


def agent_search(
    query: str,
    retrieve_fn=None,
    *,
    settings: Any | None = None,
    k: int = 6,
    max_iters: int | None = None,
    time_budget_s: float | None = None,
    assess_fn=None,
    project_id: str | None = None,
    include_global: bool = False,
) -> AgentSearchResult:
    """Run retrieve -> assess -> rewrite until facets covered or guards hit.

    ``retrieve_fn`` defaults to the single-shot ``kb_index.retrieve_evidence``
    (signature ``(query, k, *, project_id, include_global)``).
    """
    if max_iters is None:
        max_iters = int(_agent_cfg(settings, "agent_search_max_iters", 3))
    if time_budget_s is None:
        time_budget_s = float(_agent_cfg(settings, "agent_search_time_budget_s", 20.0))
    if assess_fn is None:
        # Phase 2: query-aware default — facet coverage + numeric completeness.
        assess_fn = lambda f, e: assess_gap_plus(f, e, query)  # noqa: E731

    facets = extract_facets(query)
    merged: list[Evidence] = []
    seen: set[str] = set()
    queries_issued: list[str] = []
    reason = "covered"
    start = time.monotonic()

    def _do_retrieve(q: str) -> list[Evidence]:
        if retrieve_fn is not None:
            return retrieve_fn(q, k, project_id=project_id, include_global=include_global)
        from .kb_index import retrieve_evidence

        return retrieve_evidence(q, k, project_id=project_id, include_global=include_global)

    current_query = query
    for round_no in range(1, max(1, max_iters) + 1):
        # ``>=``: a budget of 0 must mean "no rounds". With ``>`` it only did on a clock fine enough for the
        # elapsed time to be non-zero — on Windows (~15.6 ms ticks) it is exactly 0, and one round still ran.
        if time.monotonic() - start >= time_budget_s:
            reason = "budget"
            break
        queries_issued.append(current_query)
        try:
            hits = _do_retrieve(current_query) or []
        except Exception as exc:  # noqa: BLE001
            degrade_return(logger, exc, "agent search round failed (fail-open)", None)
            reason = "error"
            break
        new_hits = 0
        for ev in hits:
            key = _evidence_key(ev)
            if key and key not in seen:
                seen.add(key)
                merged.append(ev)
                new_hits += 1
        uncovered = assess_fn(facets, merged)
        logger.debug(
            "agent search round %d: %d new hits, uncovered facets=%s",
            round_no, new_hits, uncovered,
        )
        if not uncovered:
            reason = "covered"
            break
        if new_hits == 0:
            # Rewriting would just re-issue an equivalent query; stop.
            reason = "no_new"
            break
        if round_no >= max(1, max_iters):
            reason = "max_iters"
            break
        if time.monotonic() - start >= time_budget_s:
            reason = "budget"
            break
        current_query = rewrite_query(query, uncovered)

    covered = [f for f in facets if f not in assess_fn(facets, merged)]
    return AgentSearchResult(
        evidence=merged[:k] if k > 0 else merged,
        rounds=len(queries_issued),
        queries_issued=queries_issued,
        facets=facets,
        covered_facets=covered,
        stopped_reason=reason,
    )

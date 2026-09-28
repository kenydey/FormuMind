"""Phase 2 — retrieval_by_children: 子块检索、父块呈现。

Root cause: chunks are ~1600 chars and scored whole; in a long block the one
sentence that answers the query is diluted by the rest of the block, so a
parent whose best sentence is a perfect match can rank below a mediocre
whole-block match. Classic small-to-big retrieval fixes this without any
schema change:

    children (sentence/line level) → scored precisely at query time
    parent  (版式感知块: the stored 1600-char block with page_no/bbox/block_type)
            → presented as the Evidence unit (context integrity: a table row
              detached from its header is useless)

Children are derived at query time (no ingest/storage change, no second
embedding pass): BM25-style keyword scoring on children is deterministic and
cheap. Parent rank = max(best-child keyword score, parent hybrid score) so a
parent can only move *up* when one of its sentences is a strong match —
never down on the hybrid signal alone.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# CJK-aware sentence splitter: 。！？； + newlines + latin sentence ends.
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？；\n])|(?<=[.!?;]\s)")


def split_children(text: str, max_child_chars: int = 400) -> list[str]:
    """Split a parent block into sentence/line-level children."""
    text = (text or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in _SENT_SPLIT_RE.split(text) if p and p.strip()]
    children: list[str] = []
    buf = ""
    for p in parts:
        if len(p) > max_child_chars:
            if buf:
                children.append(buf)
                buf = ""
            # Long part (e.g. a table row): hard-slice, keep every piece.
            for i in range(0, len(p), max_child_chars):
                children.append(p[i : i + max_child_chars])
        elif len(buf) + len(p) + 1 <= max_child_chars:
            buf = f"{buf} {p}".strip() if buf else p
        else:
            if buf:
                children.append(buf)
            buf = p
    if buf:
        children.append(buf)
    return children or [text]


def _tokens(text: str) -> set[str]:
    """CJK-aware token set via the shared BM25 tokenizer (jieba when present).

    A naive ``\\w+`` regex glues CJK runs into one token, so query terms never
    match — that was a real bug caught by test_child_keyword_score, fixed by
    reusing ``rag._bm25_tokenize``.
    """
    from .rag import _bm25_tokenize

    try:
        return {t.lower() for t in _bm25_tokenize(text or "") if t and t.strip()}
    except Exception:
        return set(re.findall(r"\w+", (text or "").lower()))


def child_keyword_score(query_tokens: set[str], child: str) -> float:
    """Deterministic keyword overlap score for one child (0..1)."""
    if not query_tokens:
        return 0.0
    hits = query_tokens & _tokens(child)
    return len(hits) / len(query_tokens)


def children_rerank(
    query: str,
    scored: list,
    *,
    candidate_mult: int = 3,
    top_k: int = 6,
) -> list:
    """Re-rank parent ScoredChunks by best-child keyword score.

    ``scored``: list of ScoredChunk (chunk + hybrid_score), already sorted by
    hybrid desc. Takes the top ``top_k * candidate_mult`` parents, scores each
    parent's children, and returns parents ordered by
    ``max(best_child_score, hybrid_score)`` desc, truncated to ``top_k``.

    Parents keep their original hybrid_score (channel scores untouched for the
    probe); the child score is only a promotion signal.
    """
    if not scored or top_k <= 0:
        return []
    qt = _tokens(query)
    pool = scored[: max(top_k * max(1, candidate_mult), top_k)]
    ranked: list[tuple[float, object, float]] = []
    for s in pool:
        text = getattr(s.chunk, "text", "") or ""
        best = 0.0
        for child in split_children(text):
            sc = child_keyword_score(qt, child)
            if sc > best:
                best = sc
                if best >= 1.0:
                    break
        promoted = max(best, float(getattr(s, "hybrid_score", 0.0) or 0.0))
        ranked.append((promoted, s, best))
    ranked.sort(key=lambda t: t[0], reverse=True)
    return [s for _, s, _ in ranked[:top_k]]

"""Hybrid BM25 + vector retrieval for the knowledge base.

Combines BM25Okapi sparse (keyword) scoring with dense embedding (cosine)
similarity via weighted linear combination.  No external service required —
purely local.

Task 2.5: hybrid 检索——BM25 + vector 混合排序 + 端点 + 3 测试
"""
from __future__ import annotations

import logging

import numpy as np
from rank_bm25 import BM25Okapi

from ..config import get_settings
from ..domain.schemas import DocumentChunkResponse
from . import kb_index
from .errors import degrade_return

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> list[str]:
    """Tokenize text for BM25, using jieba for Chinese when available."""
    text = (text or "").strip()
    if not text:
        return []
    # Detect if text contains CJK characters
    has_cjk = any("\u4e00" <= c <= "\u9fff" or "\u3400" <= c <= "\u4dbf" for c in text)
    if has_cjk:
        try:
            import jieba

            return list(jieba.cut(text))
        except ImportError:
            logger.warning("jieba not installed; falling back to character-level tokenization for Chinese")
            return list(text)
    return text.lower().split()


def hybrid_search(
    query: str,
    top_k: int = 10,
    alpha: float = 0.3,
) -> list[DocumentChunkResponse]:
    """BM25 + vector hybrid retrieval over the persistent KB chunk store.

    Parameters
    ----------
    query : str
        Search query.
    top_k : int
        Max results to return (default 10).
    alpha : float
        BM25 weight in [0, 1]; (1-alpha) is the cosine weight.  Default 0.3
        biases slightly toward semantic matches.

    Returns
    -------
    list[DocumentChunkResponse]
        Ranked chunks, or [] when the corpus is empty / KB is disabled.
    """
    if alpha < 0.0 or alpha > 1.0:
        raise ValueError(f"alpha must be in [0, 1], got {alpha}")

    if not kb_index.kb_enabled() or not (query or "").strip() or top_k <= 0:
        return []

    try:
        from ..db.chunk_store import get_chunk_store

        chunks = get_chunk_store().all_chunks(
            limit=get_settings().kb_search_scan_limit,
        )
        if not chunks:
            return []

        n = len(chunks)

        # ── BM25 sparse scores ──────────────────────────────────────────
        corpus_tokens = [_tokenize(c.text) for c in chunks]
        bm25 = BM25Okapi(corpus_tokens)
        bm25_scores: np.ndarray = bm25.get_scores(_tokenize(query))

        # ── cosine (dense) scores ───────────────────────────────────────
        cosine_scores = np.zeros(n, dtype=float)
        query_vecs = kb_index._embed_texts([query])
        query_vec = query_vecs[0] if query_vecs else None
        if query_vec is not None:
            for i, c in enumerate(chunks):
                if c.embedding:
                    cosine_scores[i] = sum(
                        a * b for a, b in zip(query_vec, c.embedding)
                    )

        # ── normalise each score vector to [0, 1] ────────────────────────
        bm25_max = float(bm25_scores.max()) if bm25_scores.size else 0.0
        if bm25_max > 0.0:
            bm25_scores = bm25_scores / bm25_max

        cosine_max = float(cosine_scores.max()) if cosine_scores.size else 0.0
        if cosine_max > 0.0:
            cosine_scores = cosine_scores / cosine_max

        # ── weighted combination ─────────────────────────────────────────
        combined = alpha * bm25_scores + (1.0 - alpha) * cosine_scores

        # ── rank by combined score (descending) ──────────────────────────
        ranked = [
            (float(combined[i]), chunks[i])
            for i in range(n)
            if float(combined[i]) > 0.0
        ]
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        ranked = ranked[:top_k]

        # ── convert to response objects ──────────────────────────────────
        results: list[DocumentChunkResponse] = []
        for _score, c in ranked:
            results.append(
                DocumentChunkResponse(
                    id=c.id,
                    source_id=c.source_id,
                    ord=c.ord,
                    text=c.text,
                    heading_path=c.heading_path or "",
                    page=c.page_no,
                    paragraph=c.paragraph_idx
                    if c.paragraph_idx is not None
                    else (c.meta or {}).get("paragraph_idx"),
                    offset_start=c.offset_start
                    if c.offset_start is not None
                    else (c.meta or {}).get("offset_start"),
                    offset_end=c.offset_end
                    if c.offset_end is not None
                    else (c.meta or {}).get("offset_end"),
                    meta=c.meta,
                )
            )
        return results

    except Exception as exc:
        return degrade_return(logger, exc, "hybrid search failed", [])

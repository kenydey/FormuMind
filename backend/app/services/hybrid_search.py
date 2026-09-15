"""Hybrid BM25 + vector retrieval for the knowledge base.

Combines BM25Okapi sparse (keyword) scoring with dense embedding (cosine)
similarity via weighted linear combination.  No external service required —
purely local.

Task 2.5: hybrid 检索——BM25 + vector 混合排序 + 端点 + 3 测试
Dim-3: hybrid_search_scored exposes per-channel scores for /api/kb/query-test.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from rank_bm25 import BM25Okapi

from ..config import get_settings
from ..domain.schemas import DocumentChunkResponse
from . import kb_index
from .errors import degrade_return

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScoredChunk:
    """Chunk plus normalized retrieval scores for the query-test probe."""

    chunk: object
    bm25_score: float
    cosine_score: float
    hybrid_score: float


def _tokenize(text: str) -> list[str]:
    """Tokenize text for BM25, using jieba for Chinese when available."""
    text = (text or "").strip()
    if not text:
        return []
    has_cjk = any("\u4e00" <= c <= "\u9fff" or "\u3400" <= c <= "\u4dbf" for c in text)
    if has_cjk:
        try:
            import jieba

            return list(jieba.cut(text))
        except ImportError:
            logger.warning(
                "jieba not installed; falling back to character-level tokenization for Chinese"
            )
            return list(text)
    return text.lower().split()


def _to_response(c) -> DocumentChunkResponse:
    return DocumentChunkResponse(
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


def hybrid_search_scored(
    query: str,
    top_k: int = 10,
    alpha: float = 0.3,
    *,
    project_id: str | None = None,
    include_global: bool = False,
) -> list[ScoredChunk]:
    """BM25 + vector hybrid retrieval with bm25/cosine/hybrid scores retained.

    ``include_global`` only applies when ``project_id`` is set (ChunkStore
    semantics matching Hub source listing).
    """
    if alpha < 0.0 or alpha > 1.0:
        raise ValueError(f"alpha must be in [0, 1], got {alpha}")

    if not kb_index.kb_enabled() or not (query or "").strip() or top_k <= 0:
        return []

    try:
        from ..db.chunk_store import get_chunk_store

        chunks = get_chunk_store().all_chunks(
            limit=get_settings().kb_search_scan_limit,
            project_id=project_id,
            include_global=include_global,
        )
        if not chunks:
            return []

        tokenized = [(c, t) for c, t in zip(chunks, (_tokenize(c.text) for c in chunks)) if t]
        if not tokenized:
            return []
        chunks = [c for c, _ in tokenized]
        corpus_tokens = [t for _, t in tokenized]
        n = len(chunks)

        bm25 = BM25Okapi(corpus_tokens)
        bm25_scores: np.ndarray = bm25.get_scores(_tokenize(query)).astype(float)

        cosine_scores = np.zeros(n, dtype=float)
        model_cols: set[str] = {
            c.embedding_model for c in chunks if getattr(c, "embedding_model", None)
        }
        for mname in sorted(model_cols):
            from .rag import bge_query_prefix

            vecs = kb_index._embed_texts([bge_query_prefix(mname) + query], mname)
            if not vecs or not vecs[0]:
                continue
            qv = vecs[0]
            dim = len(qv)
            for i, c in enumerate(chunks):
                if c.embedding_model == mname and kb_index.comparable_embedding(c, dim, mname):
                    cosine_scores[i] = kb_index._dot(qv, c.embedding)

        bm25_max = float(bm25_scores.max()) if bm25_scores.size else 0.0
        if bm25_max > 0.0:
            bm25_scores = bm25_scores / bm25_max

        cosine_max = float(cosine_scores.max()) if cosine_scores.size else 0.0
        if cosine_max > 0.0:
            cosine_scores = cosine_scores / cosine_max

        combined = alpha * bm25_scores + (1.0 - alpha) * cosine_scores

        order = [i for i in range(n) if float(combined[i]) > 0.0]
        order.sort(key=lambda i: float(combined[i]), reverse=True)
        order = order[:top_k]

        if not order:
            qtoks = set(_tokenize(query))
            order = [i for i in range(n) if qtoks & set(corpus_tokens[i])]
            order.sort(key=lambda i: float(combined[i]), reverse=True)
            order = order[:top_k]

        return [
            ScoredChunk(
                chunk=chunks[i],
                bm25_score=round(float(bm25_scores[i]), 6),
                cosine_score=round(float(cosine_scores[i]), 6),
                hybrid_score=round(float(combined[i]), 6),
            )
            for i in order
        ]
    except Exception as exc:
        return degrade_return(logger, exc, "hybrid search scored failed", [])


def hybrid_search(
    query: str,
    top_k: int = 10,
    alpha: float = 0.3,
) -> list[DocumentChunkResponse]:
    """BM25 + vector hybrid retrieval over the persistent KB chunk store.

    Existing callers keep the unscored DocumentChunkResponse list over the
    global corpus (no project filter).
    """
    if alpha < 0.0 or alpha > 1.0:
        raise ValueError(f"alpha must be in [0, 1], got {alpha}")

    if not kb_index.kb_enabled() or not (query or "").strip() or top_k <= 0:
        return []

    try:
        scored = hybrid_search_scored(query, top_k=top_k, alpha=alpha)
        return [_to_response(s.chunk) for s in scored]
    except Exception as exc:
        return degrade_return(logger, exc, "hybrid search failed", [])

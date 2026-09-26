"""Hybrid BM25 + vector retrieval for the knowledge base.

Combines BM25Okapi sparse (keyword) scoring with dense embedding (cosine)
similarity via weighted linear combination.  No external service required —
purely local.

Task 2.5: hybrid 检索——BM25 + vector 混合排序 + 端点 + 3 测试
Dim-3: hybrid_search_scored exposes per-channel scores for /api/kb/query-test.

Post-A′ #3: latency ring (p50/p95) + scan/p95-gated BM25 candidate prefilter
before cosine (in-process; **not** Qdrant; **not** session ``rag.BM25FAISSStore``).

Option A stage-2: when the ANN gate is on and embedding dim ≥
``kb_hybrid_ann_matrix_min_dim``, score the BM25 candidate subset with a
process-local float32 matmul (still no external vector DB). Sticky hysteresis
keeps the gate warm for a few queries after p95 cools.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np
from rank_bm25 import BM25Okapi

from ..config import get_settings
from ..domain.schemas import DocumentChunkResponse
from . import kb_index
from .errors import degrade_return

logger = logging.getLogger(__name__)

# Ring buffer of recent hybrid_search wall times (ms). Process-local only.
_LATENCY_MS: deque[float] = deque(maxlen=128)
_LATENCY_LOCK = threading.Lock()
_LAST_ANN_ACTIVE = False
_LAST_ANN_MATRIX = False
_ANN_STREAK = 0
_ANN_STICKY_REMAINING = 0


@dataclass(frozen=True)
class ScoredChunk:
    """Chunk plus normalized retrieval scores for the query-test probe."""

    chunk: object
    bm25_score: float
    cosine_score: float
    hybrid_score: float


def reset_latency_stats() -> None:
    """Test helper — clear the in-process latency ring."""
    global _LAST_ANN_ACTIVE, _LAST_ANN_MATRIX, _ANN_STREAK, _ANN_STICKY_REMAINING
    with _LATENCY_LOCK:
        _LATENCY_MS.clear()
        _LAST_ANN_ACTIVE = False
        _LAST_ANN_MATRIX = False
        _ANN_STREAK = 0
        _ANN_STICKY_REMAINING = 0


def record_hybrid_latency_ms(ms: float) -> None:
    try:
        v = float(ms)
    except (TypeError, ValueError):
        return
    if v < 0:
        return
    with _LATENCY_LOCK:
        _LATENCY_MS.append(v)


def hybrid_latency_stats() -> dict[str, Any]:
    """p50/p95 over the recent ring; empty → zeros."""
    with _LATENCY_LOCK:
        samples = list(_LATENCY_MS)
        ann = bool(_LAST_ANN_ACTIVE)
        matrix = bool(_LAST_ANN_MATRIX)
        streak = int(_ANN_STREAK)
    if not samples:
        return {
            "n": 0,
            "p50_ms": None,
            "p95_ms": None,
            "ann_last": ann,
            "ann_matrix_last": matrix,
            "ann_streak": streak,
            "note": "persistent hybrid_search ≠ rag.BM25FAISSStore (session RAG)",
        }
    arr = np.asarray(samples, dtype=float)
    return {
        "n": int(arr.size),
        "p50_ms": round(float(np.percentile(arr, 50)), 2),
        "p95_ms": round(float(np.percentile(arr, 95)), 2),
        "ann_last": ann,
        "ann_matrix_last": matrix,
        "ann_streak": streak,
        "note": "persistent hybrid_search ≠ rag.BM25FAISSStore (session RAG)",
    }


def _should_use_ann_gate(*, corpus_n: int, settings) -> bool:
    """Gate on scan pressure, elevated p95, or sticky hysteresis after a fire."""
    scan_limit = max(1, int(getattr(settings, "kb_search_scan_limit", 5000) or 5000))
    near_cap = corpus_n >= int(scan_limit * 0.9)
    p95_thresh = float(getattr(settings, "kb_hybrid_ann_gate_p95_ms", 800.0) or 800.0)
    stats = hybrid_latency_stats()
    p95 = stats.get("p95_ms")
    hot = p95 is not None and float(p95) >= p95_thresh and int(stats.get("n") or 0) >= 5
    with _LATENCY_LOCK:
        sticky = _ANN_STICKY_REMAINING > 0
    return bool(near_cap or hot or sticky)


def _ann_gate_base(*, corpus_n: int, settings) -> bool:
    """Pressure/p95 only (no sticky) — used to refresh hysteresis budget."""
    scan_limit = max(1, int(getattr(settings, "kb_search_scan_limit", 5000) or 5000))
    near_cap = corpus_n >= int(scan_limit * 0.9)
    p95_thresh = float(getattr(settings, "kb_hybrid_ann_gate_p95_ms", 800.0) or 800.0)
    stats = hybrid_latency_stats()
    p95 = stats.get("p95_ms")
    hot = p95 is not None and float(p95) >= p95_thresh and int(stats.get("n") or 0) >= 5
    return bool(near_cap or hot)


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


def _cosine_on_indices(
    query: str,
    chunks: list,
    indices: list[int],
    cosine_scores: np.ndarray,
    *,
    use_matrix: bool = False,
    matrix_min_dim: int = 512,
) -> bool:
    """Fill cosine_scores only for ``indices`` (ANN / prefilter path).

    Returns True when the float32 matmul path was used for at least one model.
    """
    subset = [chunks[i] for i in indices]
    model_cols: set[str] = {
        c.embedding_model for c in subset if getattr(c, "embedding_model", None)
    }
    used_matrix = False
    for mname in sorted(model_cols):
        from .rag import bge_query_prefix

        vecs = kb_index._embed_texts([bge_query_prefix(mname) + query], mname)
        if not vecs or not vecs[0]:
            continue
        qv = vecs[0]
        dim = len(qv)
        if use_matrix and dim >= int(matrix_min_dim):
            if _cosine_matrix_for_model(qv, chunks, indices, cosine_scores, mname, dim):
                used_matrix = True
                continue
        for i in indices:
            c = chunks[i]
            if c.embedding_model == mname and kb_index.comparable_embedding(c, dim, mname):
                cosine_scores[i] = kb_index._dot(qv, c.embedding)
    return used_matrix


def _cosine_matrix_for_model(
    qv: list[float],
    chunks: list,
    indices: list[int],
    cosine_scores: np.ndarray,
    mname: str,
    dim: int,
) -> bool:
    """Score comparable rows for one model via ``mat @ q`` (normalized vectors)."""
    rows: list[list[float]] = []
    valid: list[int] = []
    for i in indices:
        c = chunks[i]
        if c.embedding_model != mname or not kb_index.comparable_embedding(c, dim, mname):
            continue
        emb = getattr(c, "embedding", None)
        if not emb or len(emb) != dim:
            continue
        rows.append(emb)
        valid.append(i)
    if not rows:
        return False
    mat = np.asarray(rows, dtype=np.float32)
    q = np.asarray(qv, dtype=np.float32)
    scores = mat @ q
    for i, s in zip(valid, scores):
        cosine_scores[i] = float(s)
    return True


def hybrid_search_scored(
    query: str,
    top_k: int = 10,
    alpha: float | None = None,
    *,
    project_id: str | None = None,
    include_global: bool = False,
) -> list[ScoredChunk]:
    """BM25 + vector hybrid retrieval with bm25/cosine/hybrid scores retained.

    ``include_global`` only applies when ``project_id`` is set (ChunkStore
    semantics matching Hub source listing). When ``alpha`` is omitted, uses
    ``settings.kb_hybrid_alpha`` so the retrieval probe and recommend path share
    one knobs.

    When scan is near cap or recent p95 exceeds threshold (or sticky
    hysteresis), cosine is only computed on the top-N BM25 candidates. For
    embedding dim ≥ ``kb_hybrid_ann_matrix_min_dim``, that subset is scored
    with a process-local float32 matmul (Option A; still not Qdrant).
    """
    global _LAST_ANN_ACTIVE, _LAST_ANN_MATRIX, _ANN_STREAK, _ANN_STICKY_REMAINING
    settings = get_settings()
    if alpha is None:
        alpha = float(settings.kb_hybrid_alpha)
    if alpha < 0.0 or alpha > 1.0:
        raise ValueError(f"alpha must be in [0, 1], got {alpha}")

    if not kb_index.kb_enabled() or not (query or "").strip() or top_k <= 0:
        return []

    t0 = time.perf_counter()
    ann_active = False
    ann_matrix = False
    ann_base = False
    try:
        from ..db.chunk_store import get_chunk_store

        chunks = get_chunk_store().all_chunks(
            limit=settings.kb_search_scan_limit,
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
        bm25_raw: np.ndarray = bm25.get_scores(_tokenize(query)).astype(float)

        ann_base = _ann_gate_base(corpus_n=n, settings=settings)
        ann_active = _should_use_ann_gate(corpus_n=n, settings=settings)
        cosine_scores = np.zeros(n, dtype=float)

        if ann_active:
            pool = max(
                top_k * 4,
                int(getattr(settings, "kb_hybrid_ann_candidate_pool", 800) or 800),
            )
            pool = min(pool, n)
            # Top-BM25 indices for cosine (keep zeros elsewhere → BM25-only for tail).
            top_idx = np.argsort(-bm25_raw)[:pool].tolist()
            matrix_min = int(
                getattr(settings, "kb_hybrid_ann_matrix_min_dim", 512) or 512
            )
            ann_matrix = _cosine_on_indices(
                query,
                chunks,
                top_idx,
                cosine_scores,
                use_matrix=True,
                matrix_min_dim=matrix_min,
            )
        else:
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

        bm25_scores = bm25_raw.copy()
        bm25_max = float(bm25_scores.max()) if bm25_scores.size else 0.0
        if bm25_max > 0.0:
            bm25_scores = bm25_scores / bm25_max

        cosine_max = float(cosine_scores.max()) if cosine_scores.size else 0.0
        if cosine_max > 0.0:
            cosine_scores = cosine_scores / cosine_max

        combined = alpha * bm25_scores + (1.0 - alpha) * cosine_scores

        order = [i for i in range(n) if float(combined[i]) > 0.0]
        order.sort(key=lambda i: float(combined[i]), reverse=True)

        if not order:
            qtoks = set(_tokenize(query))
            order = [i for i in range(n) if qtoks & set(corpus_tokens[i])]
            order.sort(key=lambda i: float(combined[i]), reverse=True)

        # Corpus quality gate (blocked origin_url / garbage / wiki) — shared by
        # Hub retrieval probe and recommend hybrid fuse. Fills top_k after drops.
        from .kb_retrieval_gate import gate_chunk_indices

        order = gate_chunk_indices(chunks, order, top_k=top_k)

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
    finally:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        record_hybrid_latency_ms(elapsed_ms)
        sticky_budget = int(
            getattr(settings, "kb_hybrid_ann_sticky_queries", 3) or 3
        )
        with _LATENCY_LOCK:
            _LAST_ANN_ACTIVE = ann_active
            _LAST_ANN_MATRIX = ann_matrix
            if ann_active:
                _ANN_STREAK += 1
            else:
                _ANN_STREAK = 0
            if ann_base:
                _ANN_STICKY_REMAINING = max(0, sticky_budget)
            elif _ANN_STICKY_REMAINING > 0:
                _ANN_STICKY_REMAINING -= 1


def hybrid_search(
    query: str,
    top_k: int = 10,
    alpha: float | None = None,
) -> list[DocumentChunkResponse]:
    """BM25 + vector hybrid retrieval over the persistent KB chunk store.

    Existing callers keep the unscored DocumentChunkResponse list over the
    global corpus (no project filter). ``alpha`` defaults to ``kb_hybrid_alpha``.

    Note: this is **not** the session-level ``rag.BM25FAISSStore`` used by chat.
    """
    if alpha is None:
        alpha = float(get_settings().kb_hybrid_alpha)
    if alpha < 0.0 or alpha > 1.0:
        raise ValueError(f"alpha must be in [0, 1], got {alpha}")

    if not kb_index.kb_enabled() or not (query or "").strip() or top_k <= 0:
        return []

    try:
        scored = hybrid_search_scored(query, top_k=top_k, alpha=alpha)
        return [_to_response(s.chunk) for s in scored]
    except Exception as exc:
        return degrade_return(logger, exc, "hybrid search failed", [])

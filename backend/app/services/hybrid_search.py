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
from .text_tokenize import tokenize as _tokenize  # P1-1: unified tokenizer

logger = logging.getLogger(__name__)

# Ring buffer of recent hybrid_search wall times (ms). Process-local only.
_LATENCY_MS: deque[float] = deque(maxlen=128)
_LATENCY_LOCK = threading.Lock()
_LAST_ANN_ACTIVE = False
_LAST_ANN_MATRIX = False
_LAST_ANN_FAISS = False
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
    global _LAST_ANN_ACTIVE, _LAST_ANN_MATRIX, _LAST_ANN_FAISS, _ANN_STREAK, _ANN_STICKY_REMAINING
    with _LATENCY_LOCK:
        _LATENCY_MS.clear()
        _LAST_ANN_ACTIVE = False
        _LAST_ANN_MATRIX = False
        _LAST_ANN_FAISS = False
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
        faiss = bool(_LAST_ANN_FAISS)
        streak = int(_ANN_STREAK)
    if not samples:
        return {
            "n": 0,
            "p50_ms": None,
            "p95_ms": None,
            "ann_last": ann,
            "ann_matrix_last": matrix,
            "ann_faiss_last": faiss,
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
        "ann_faiss_last": faiss,
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


def _null_model_second_pass(
    chunks: list,
    indices: list[int],
    cosine_scores: np.ndarray,
    model_cols: set[str],
    query: str,
) -> None:
    """v25-fix: NULL-model chunk 第二遍（ANN-gate / 非 gate 路径共享）。

    legacy 行（embedding_model IS NULL）在第一遍中永远不会被打分
    （第一遍只处理 ``c.embedding_model == mname`` 的行，含矩阵路径），
    所以这里只处理 NULL-model 行，无需浮点哨兵。query 向量按模型预计算一次。

    v27 P1-7: 全 legacy 语料时 model_cols 为空 —— 回退当前默认模型，
    否则 query 向量根本不生成，向量通道静默失效。
    """
    from .rag import bge_query_prefix, embed_model_name

    null_idx = [i for i in indices if getattr(chunks[i], "embedding_model", None) is None]
    if not null_idx:
        return
    if not model_cols:
        model_cols = {embed_model_name()}
    qv_by_model: dict[str, tuple[list[float], int]] = {}
    for mname in sorted(model_cols):
        vecs = kb_index._embed_texts([bge_query_prefix(mname) + query], mname)
        if not vecs or not vecs[0]:
            continue
        qv_by_model[mname] = (vecs[0], len(vecs[0]))
    for i in null_idx:
        c = chunks[i]
        for mname, (qv, dim) in qv_by_model.items():
            # U-2: comparable_embedding 返回 (ok, vec)，不再二次反序列化
            ok, cemb = kb_index.comparable_embedding(c, dim, mname)
            if ok and cemb:
                cosine_scores[i] = kb_index._dot(qv, cemb)
                break


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
            if c.embedding_model == mname:
                # U-2: comparable_embedding 返回 (ok, vec)，不再二次反序列化
                ok, cemb = kb_index.comparable_embedding(c, dim, mname)
                if ok and cemb:
                    cosine_scores[i] = kb_index._dot(qv, cemb)
    # v25-fix: NULL-model 第二遍抽为共享函数（gate/非 gate 路径统一），
    # 与 comparable_embedding docstring 承诺对齐。
    _null_model_second_pass(chunks, indices, cosine_scores, model_cols, query)
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
        if c.embedding_model != mname:
            continue
        # U-2: comparable_embedding 返回 (ok, vec)，不再二次反序列化
        ok, emb = kb_index.comparable_embedding(c, dim, mname)
        if not ok or not emb or len(emb) != dim:
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


def _faiss_vector_scores(
    query: str,
    settings,
    *,
    top_k: int,
    project_id: str | None,
    include_global: bool,
) -> dict[str, float] | None:
    """C-1: full-corpus ANN vector scores, ``{chunk_id: cosine}``.

    Searches each faiss model bucket with a query vector embedded by that
    bucket's model (same per-model rule as the brute-force path). Returns
    ``None`` on *any* failure — missing package, disabled setting, empty
    index, embedding failure — so the caller transparently falls back to
    the pre-C-1 path. Never raises.
    """
    try:
        if not getattr(settings, "kb_ann_enabled", True):
            return None
        from .kb_ann import ensure_index
        from .kb_ann import search as ann_search
        from .rag import bge_query_prefix

        idx = ensure_index()
        if not idx.get("ready"):
            return None
        buckets = (idx.get("buckets") or {}).get("buckets", {})
        if not buckets:
            return None
        pool = max(
            top_k * 4,
            int(getattr(settings, "kb_hybrid_ann_candidate_pool", 800) or 800),
        )
        # v18-20: 项目隔离查询时全局 ANN 命中被稀释 —— pool 放大 3 倍，
        # 确保项目过滤后仍有足够候选（否则向量通道名存实亡）。
        if project_id:
            pool = pool * 3
        out: dict[str, float] = {}
        for model in buckets:
            vecs = kb_index._embed_texts(
                [bge_query_prefix(model) + query], model or None
            )
            if not vecs or not vecs[0]:
                continue
            hits = ann_search(model, vecs[0], pool)
            if not hits:
                continue
            for cid, score in hits:
                if cid not in out or score > out[cid]:
                    out[cid] = float(score)
        return out or None
    except Exception as exc:  # noqa: BLE001
        logger.debug("kb_ann vector path failed, falling back: %s", exc)
        return None


def hybrid_search_scored(
    query: str,
    top_k: int = 10,
    alpha: float | None = None,
    *,
    project_id: str | None = None,
    include_global: bool = True,
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
    global _LAST_ANN_ACTIVE, _LAST_ANN_MATRIX, _LAST_ANN_FAISS, _ANN_STREAK, _ANN_STICKY_REMAINING
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
    ann_faiss = False
    try:
        from ..db.chunk_store import get_chunk_store

        scan_limit = max(1, int(getattr(settings, "kb_search_scan_limit", 5000) or 5000))
        chunks = get_chunk_store().all_chunks(
            limit=scan_limit,
            project_id=project_id,
            include_global=include_global,
        )
        if not chunks:
            return []
        if len(chunks) >= scan_limit:
            logger.warning(
                "hybrid_search scan at cap: n=%d limit=%d project_id=%s "
                "include_global=%s — recall may truncate older chunks",
                len(chunks),
                scan_limit,
                project_id or "-",
                include_global,
            )

        # C-1: faiss ANN over the full vector corpus (bypasses the 5000 cap
        # for the vector half). Fail-open: None -> pre-C-1 brute-force path.
        faiss_scores = _faiss_vector_scores(
            query,
            settings,
            top_k=top_k,
            project_id=project_id,
            include_global=include_global,
        )
        ann_faiss = faiss_scores is not None
        if ann_faiss:
            have_ids = {c.id for c in chunks}
            extra_ids = [cid for cid in faiss_scores if cid not in have_ids]
            if extra_ids:
                extras = get_chunk_store().chunks_by_ids(
                    extra_ids,
                    project_id,
                    include_global=include_global,
                )
                if extras:
                    chunks = chunks + extras

        # v19-fix: 空 token 但有向量的 chunk 不丢弃（BM25 得 0，但 cosine 可得分）。
        # 否则向量再相关也搜不到 —— 静默召回损失。
        tokenized = [
            (c, t)
            for c, t in zip(chunks, (_tokenize(c.text) for c in chunks))
            if t or getattr(c, "embedding", None)
        ]
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

        if ann_faiss:
            # C-1: exact ANN scores (IndexFlatIP over normalised vectors ==
            # brute-force cosine). Chunks absent from the faiss result keep 0.
            for i, c in enumerate(chunks):
                cosine_scores[i] = float(faiss_scores.get(c.id, 0.0) or 0.0)
        elif ann_active:
            pool = max(
                top_k * 4,
                int(getattr(settings, "kb_hybrid_ann_candidate_pool", 800) or 800),
            )
            pool = min(pool, n)
            # Top-BM25 indices for cosine (keep zeros elsewhere → BM25-only for tail).
            # v22-note: 空 token chunk（bm25=0）在 ANN gate 下 cosine 恒 0 ——
            # 性能权衡（小库/冷路径走全量 cosine，不受影响）。
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
                    if c.embedding_model == mname:
                        # U-2: comparable_embedding 返回 (ok, vec)，不再二次反序列化
                        ok, cemb = kb_index.comparable_embedding(c, dim, mname)
                        if ok and cemb:
                            cosine_scores[i] = kb_index._dot(qv, cemb)
            # v25-fix: 非 gate 路径也补 NULL-model 第二遍（v23 只修了 gate 路径）。
            _null_model_second_pass(chunks, list(range(len(chunks))), cosine_scores, model_cols, query)

        bm25_scores = bm25_raw.copy()
        bm25_max = float(bm25_scores.max()) if bm25_scores.size else 0.0
        if bm25_max > 0.0:
            bm25_scores = bm25_scores / bm25_max

        cosine_max = float(cosine_scores.max()) if cosine_scores.size else 0.0
        if cosine_max > 0.0:
            cosine_scores = cosine_scores / cosine_max

        # P2 A/B: kb_hybrid_entity_boost —— 化学实体加成移到融合前。
        # legacy search_chunks 是 cosine 0-1 尺度上的加性 0.2/0.3；直接加到
        # RRF 融合分（~0.02）上会主导排序。此处对归一化分量（0-1 尺度）做
        # 加性 boost（与 legacy 同量级），再进融合，而非加到融合后的 RRF 分上。
        # 默认关，A/B 验证后再决定。
        if getattr(settings, "kb_hybrid_entity_boost", False):
            try:
                qctx = kb_index._query_chem_context(query)
                if qctx["cas"] or qctx["formulas"] or qctx["smiles"] or qctx["products"]:
                    boosts = np.array(
                        [kb_index._entity_boost(c, qctx) for c in chunks],
                        dtype=float,
                    )
                    bm25_scores = bm25_scores + boosts
                    cosine_scores = cosine_scores + boosts
            except Exception:  # noqa: BLE001
                logger.debug("hybrid entity boost skipped (fail-open)", exc_info=True)

        fusion = (getattr(settings, "kb_hybrid_fusion", None) or "weighted").strip().lower()
        if fusion == "rrf":
            # Wave D: Reciprocal Rank Fusion (k=60). Scores stored as RRF mass
            # for hybrid_score; channel scores remain normalized BM25/cosine.
            rrf_k = 60.0
            bm25_rank = np.argsort(-bm25_scores)
            cos_rank = np.argsort(-cosine_scores)
            bm25_pos = {int(idx): rank for rank, idx in enumerate(bm25_rank)}
            cos_pos = {int(idx): rank for rank, idx in enumerate(cos_rank)}
            combined = np.zeros(n, dtype=float)
            for i in range(n):
                combined[i] = 1.0 / (rrf_k + bm25_pos[i] + 1) + 1.0 / (
                    rrf_k + cos_pos[i] + 1
                )
        else:
            combined = alpha * bm25_scores + (1.0 - alpha) * cosine_scores

        order = [i for i in range(n) if float(combined[i]) > 0.0]
        order.sort(key=lambda i: float(combined[i]), reverse=True)

        if not order:
            # v7: fallback 按 token 交集大小排序（此前按全≤0 的 combined 排，无意义）。
            qtoks = set(_tokenize(query))
            _overlap = [(len(qtoks & set(corpus_tokens[i])), i) for i in range(n)]
            _overlap = [(ov, i) for ov, i in _overlap if ov > 0]
            _overlap.sort(reverse=True)
            order = [i for _, i in _overlap]

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
            _LAST_ANN_FAISS = ann_faiss
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
    *,
    project_id: str | None = None,
    include_global: bool = True,
) -> list[DocumentChunkResponse]:
    """BM25 + vector hybrid retrieval over the persistent KB chunk store.

    Returns the unscored DocumentChunkResponse list. ``alpha`` defaults to
    ``kb_hybrid_alpha``; ``project_id``/``include_global`` filter the corpus.

    Note: this is **not** the session-level ``rag.BM25FAISSStore`` used by chat.
    """
    if alpha is None:
        alpha = float(get_settings().kb_hybrid_alpha)
    if alpha < 0.0 or alpha > 1.0:
        raise ValueError(f"alpha must be in [0, 1], got {alpha}")

    if not kb_index.kb_enabled() or not (query or "").strip() or top_k <= 0:
        return []

    try:
        scored = hybrid_search_scored(
            query,
            top_k=top_k,
            alpha=alpha,
            project_id=project_id,
            include_global=include_global,
        )
        return [_to_response(s.chunk) for s in scored]
    except Exception as exc:
        return degrade_return(logger, exc, "hybrid search failed", [])

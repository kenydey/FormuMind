"""Option A stage-2: in-process matrix cosine on ANN-gated BM25 subset."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from app.services import hybrid_search as hs


def test_ann_sticky_keeps_gate_warm():
    hs.reset_latency_stats()
    settings = SimpleNamespace(
        kb_search_scan_limit=5000,
        kb_hybrid_ann_gate_p95_ms=800.0,
        kb_hybrid_ann_sticky_queries=3,
    )
    # Heat p95 so base gate fires.
    for _ in range(6):
        hs.record_hybrid_latency_ms(1200.0)
    assert hs._ann_gate_base(corpus_n=10, settings=settings) is True
    assert hs._should_use_ann_gate(corpus_n=10, settings=settings) is True

    # Cool samples so base is false; keep sticky budget so gate stays on.
    with hs._LATENCY_LOCK:
        hs._LATENCY_MS.clear()
        hs._ANN_STICKY_REMAINING = 2
        hs._LAST_ANN_ACTIVE = True
        hs._ANN_STREAK = 1
    assert hs._ann_gate_base(corpus_n=10, settings=settings) is False
    assert hs._should_use_ann_gate(corpus_n=10, settings=settings) is True


def test_cosine_matrix_matches_dot_scores():
    hs.reset_latency_stats()
    dim = 768
    rng = np.random.default_rng(0)
    q = rng.standard_normal(dim)
    q = (q / np.linalg.norm(q)).astype(float).tolist()
    rows = []
    chunks = []
    for _ in range(5):
        v = rng.standard_normal(dim)
        v = (v / np.linalg.norm(v)).astype(float).tolist()
        rows.append(v)
        chunks.append(
            SimpleNamespace(embedding_model="Qwen/Qwen3-Embedding-0.6B", embedding=v)
        )
    indices = list(range(len(chunks)))
    scores_m = np.zeros(len(chunks), dtype=float)
    assert (
        hs._cosine_matrix_for_model(
            q, chunks, indices, scores_m, "Qwen/Qwen3-Embedding-0.6B", dim
        )
        is True
    )
    for i, emb in enumerate(rows):
        expected = sum(a * b for a, b in zip(q, emb))
        assert abs(scores_m[i] - expected) < 1e-5


def test_cosine_on_indices_uses_matrix_above_min_dim(monkeypatch):
    hs.reset_latency_stats()
    dim = 768
    rng = np.random.default_rng(1)
    q = rng.standard_normal(dim)
    q = (q / np.linalg.norm(q)).astype(float).tolist()
    chunks = []
    for _ in range(4):
        v = rng.standard_normal(dim)
        v = (v / np.linalg.norm(v)).astype(float).tolist()
        chunks.append(
            SimpleNamespace(embedding_model="fake/high-dim", embedding=v)
        )

    monkeypatch.setattr(
        "app.services.kb_index._embed_texts",
        lambda texts, model_name=None: [q],
    )
    monkeypatch.setattr(
        "app.services.kb_index.comparable_embedding",
        lambda chunk, d, m: True,
    )

    scores = np.zeros(len(chunks), dtype=float)
    used = hs._cosine_on_indices(
        "query",
        chunks,
        list(range(len(chunks))),
        scores,
        use_matrix=True,
        matrix_min_dim=512,
    )
    assert used is True
    assert float(scores.max()) > 0.0


def test_cosine_on_indices_keeps_dot_below_min_dim(monkeypatch):
    hs.reset_latency_stats()
    dim = 384
    rng = np.random.default_rng(2)
    q = rng.standard_normal(dim)
    q = (q / np.linalg.norm(q)).astype(float).tolist()
    chunks = []
    for _ in range(3):
        v = rng.standard_normal(dim)
        v = (v / np.linalg.norm(v)).astype(float).tolist()
        chunks.append(
            SimpleNamespace(
                embedding_model="sentence-transformers/all-MiniLM-L6-v2",
                embedding=v,
            )
        )

    monkeypatch.setattr(
        "app.services.kb_index._embed_texts",
        lambda texts, model_name=None: [q],
    )
    monkeypatch.setattr(
        "app.services.kb_index.comparable_embedding",
        lambda chunk, d, m: True,
    )

    scores = np.zeros(len(chunks), dtype=float)
    used = hs._cosine_on_indices(
        "query",
        chunks,
        list(range(len(chunks))),
        scores,
        use_matrix=True,
        matrix_min_dim=512,
    )
    assert used is False
    assert float(scores.max()) > 0.0


def test_latency_stats_expose_matrix_and_streak():
    hs.reset_latency_stats()
    with hs._LATENCY_LOCK:
        hs._LAST_ANN_ACTIVE = True
        hs._LAST_ANN_MATRIX = True
        hs._ANN_STREAK = 4
    stats = hs.hybrid_latency_stats()
    assert stats["ann_last"] is True
    assert stats["ann_matrix_last"] is True
    assert stats["ann_streak"] == 4

"""Regression guard for the in-process cosine recall ceiling (Wave 4).

Asserts that scoring 1k chunks stays well under the latency budget on both
cosine paths (plain-Python loop and float32 matmul). Thresholds are the
measured 1k p95 x3, rounded up (384d: 24.4ms -> 100ms; 512d: 63.4ms -> 200ms,
measured 2026-09-28 on this host). If this test starts failing, the corpus
has grown or the hot path regressed — see docs/vector-ceiling-2026-09-28.md.
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from app.services.kb_index import _dot

N = 1000
QUERIES = 10
THRESHOLDS_MS = {384: 100.0, 512: 200.0}


def _corpus(n: int, dim: int):
    rng = np.random.default_rng(1234)
    mat = rng.standard_normal((n, dim)).astype(np.float32)
    mat /= np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12
    return mat, [row.tolist() for row in mat]


@pytest.mark.parametrize("dim", [384, 512])
def test_cosine_recall_1k_within_budget(dim: int) -> None:
    mat, lists = _corpus(N, dim)
    rng = np.random.default_rng(99)
    queries = rng.standard_normal((QUERIES, dim)).astype(np.float32)
    queries /= np.linalg.norm(queries, axis=1, keepdims=True) + 1e-12

    worst = 0.0
    for q in queries:
        ql = q.tolist()
        t0 = time.perf_counter()
        scores = [_dot(ql, c) for c in lists]
        _ = sorted(range(N), key=scores.__getitem__, reverse=True)[:10]
        worst = max(worst, (time.perf_counter() - t0) * 1000.0)
        t0 = time.perf_counter()
        _ = np.argpartition(-(mat @ q), 10)[:10]
        worst = max(worst, (time.perf_counter() - t0) * 1000.0)

    assert worst < THRESHOLDS_MS[dim], (
        f"1k-chunk cosine recall took {worst:.1f}ms > "
        f"{THRESHOLDS_MS[dim]:.0f}ms budget at dim={dim}; "
        "see docs/vector-ceiling-2026-09-28.md"
    )

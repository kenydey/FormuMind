"""Benchmark the in-process cosine recall ceiling (Wave 4, measurement only).

Measures the two cosine paths used by hybrid_search.py at 10k/50k/100k
chunks and dims 384 (MiniLM-L6 default) / 512 (bge zh):
  - loop path : plain-Python _dot per chunk (kb_index._dot)
  - matrix path: float32 matmul (hybrid_search._cosine_matrix_for_model style)

Reports per-query p50/p95 wall time and resident memory of the matrix.
No migration, no code changes to production paths.
"""
from __future__ import annotations

import os
import resource
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.services.kb_index import _dot  # noqa: E402

SCALES = (10_000, 50_000, 100_000)
DIMS = (384, 512)
QUERIES = 30
TOP_K = 10


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def bench(n: int, dim: int) -> dict:
    rng = np.random.default_rng(42)
    mat = rng.standard_normal((n, dim)).astype(np.float32)
    mat /= np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12
    # Python lists mimic JSON-loaded embeddings from SQLite.
    lists = [row.tolist() for row in mat]
    queries = rng.standard_normal((QUERIES, dim)).astype(np.float32)
    queries /= np.linalg.norm(queries, axis=1, keepdims=True) + 1e-12

    # Path A: plain-Python loop (kb_index._dot), the non-ANN production path.
    loop_ms: list[float] = []
    for q in queries:
        ql = q.tolist()
        t0 = time.perf_counter()
        scores = [_dot(ql, c) for c in lists]
        _ = sorted(range(n), key=scores.__getitem__, reverse=True)[:TOP_K]
        loop_ms.append((time.perf_counter() - t0) * 1000.0)

    # Path B: float32 matmul (ANN stage-2 path).
    mat_ms: list[float] = []
    for q in queries:
        t0 = time.perf_counter()
        scores = mat @ q
        _ = np.argpartition(-scores, TOP_K)[:TOP_K]
        mat_ms.append((time.perf_counter() - t0) * 1000.0)

    mem_mb = rss_mb()
    return {
        "n": n,
        "dim": dim,
        "loop_p50_ms": round(float(np.percentile(loop_ms, 50)), 1),
        "loop_p95_ms": round(float(np.percentile(loop_ms, 95)), 1),
        "mat_p50_ms": round(float(np.percentile(mat_ms, 50)), 1),
        "mat_p95_ms": round(float(np.percentile(mat_ms, 95)), 1),
        "matrix_mb": round(n * dim * 4 / 1024 / 1024, 1),
        "rss_mb": round(mem_mb, 1),
    }


def main() -> None:
    rows = [bench(n, d) for d in DIMS for n in SCALES]
    print(f"{'dim':>5} {'n':>8} {'loop_p50':>9} {'loop_p95':>9} "
          f"{'mat_p50':>8} {'mat_p95':>8} {'matrix_MB':>10} {'rss_MB':>8}")
    for r in rows:
        print(f"{r['dim']:>5} {r['n']:>8} {r['loop_p50_ms']:>9} {r['loop_p95_ms']:>9} "
              f"{r['mat_p50_ms']:>8} {r['mat_p95_ms']:>8} {r['matrix_mb']:>10} {r['rss_mb']:>8}")


if __name__ == "__main__":
    main()

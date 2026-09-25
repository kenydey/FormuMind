"""Post-A′ #3: hybrid latency ring + ANN gate heuristic."""
from __future__ import annotations

from types import SimpleNamespace

from app.services import hybrid_search as hs


def test_latency_stats_empty_then_populated():
    hs.reset_latency_stats()
    empty = hs.hybrid_latency_stats()
    assert empty["n"] == 0
    assert empty["p50_ms"] is None
    assert "BM25FAISSStore" in (empty.get("note") or "")

    for ms in (10, 20, 30, 40, 50, 100, 200, 400, 800, 900):
        hs.record_hybrid_latency_ms(ms)
    stats = hs.hybrid_latency_stats()
    assert stats["n"] == 10
    assert stats["p50_ms"] is not None
    assert stats["p95_ms"] is not None
    assert stats["p95_ms"] >= stats["p50_ms"]


def test_ann_gate_on_near_cap():
    hs.reset_latency_stats()
    settings = SimpleNamespace(
        kb_search_scan_limit=100,
        kb_hybrid_ann_gate_p95_ms=800.0,
    )
    assert hs._should_use_ann_gate(corpus_n=95, settings=settings) is True
    assert hs._should_use_ann_gate(corpus_n=10, settings=settings) is False


def test_ann_gate_on_hot_p95():
    hs.reset_latency_stats()
    for _ in range(6):
        hs.record_hybrid_latency_ms(1200.0)
    settings = SimpleNamespace(
        kb_search_scan_limit=5000,
        kb_hybrid_ann_gate_p95_ms=800.0,
    )
    assert hs._should_use_ann_gate(corpus_n=10, settings=settings) is True

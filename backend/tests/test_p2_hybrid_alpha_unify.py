"""P2: BM25FAISSStore shares kb_hybrid_alpha with hybrid_search_scored."""
from __future__ import annotations

import builtins

import pytest

from app.domain.schemas import Evidence
from app.services.rag import BM25FAISSStore


def test_bm25_faiss_reads_kb_hybrid_alpha(monkeypatch):
    monkeypatch.setenv("FORMUMIND_KB_HYBRID_ALPHA", "0.42")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        store = BM25FAISSStore()
        assert store._resolved_bm25_weight() == pytest.approx(0.42)
    finally:
        get_settings.cache_clear()


def test_bm25_faiss_explicit_weight_overrides_settings(monkeypatch):
    monkeypatch.setenv("FORMUMIND_KB_HYBRID_ALPHA", "0.1")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        store = BM25FAISSStore(bm25_weight=0.75)
        assert store._resolved_bm25_weight() == pytest.approx(0.75)
    finally:
        get_settings.cache_clear()


def test_hybrid_score_uses_resolved_alpha(monkeypatch):
    """With FAISS unavailable, hybrid still applies α (BM25-only path)."""
    real_import = builtins.__import__

    def _block_st(name, *args, **kwargs):
        if name == "sentence_transformers" or name.startswith("sentence_transformers."):
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_st)
    monkeypatch.setenv("FORMUMIND_KB_HYBRID_ALPHA", "0.9")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        store = BM25FAISSStore()
        store.ingest(
            [
                Evidence(
                    source="t",
                    identifier="a",
                    title="epoxy coating barrier",
                    snippet="corrosion resistance epoxy",
                    relevance=1.0,
                ),
                Evidence(
                    source="t",
                    identifier="b",
                    title="quantum qubit",
                    snippet="superconducting circuit",
                    relevance=0.9,
                ),
            ]
        )
        hits = store.query("epoxy corrosion coating", k=2)
        assert hits and hits[0].identifier == "a"
        assert store._resolved_bm25_weight() == pytest.approx(0.9)
    finally:
        get_settings.cache_clear()

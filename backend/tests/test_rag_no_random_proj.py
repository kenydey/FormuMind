"""BM25FAISSStore must not invent random vectors when embeddings are missing."""
from __future__ import annotations

import builtins

import pytest

from app.domain.schemas import Evidence
from app.services.rag import BM25FAISSStore


def test_missing_sentence_transformers_degrades_to_bm25(monkeypatch):
    real_import = builtins.__import__

    def _block_st(name, *args, **kwargs):
        if name == "sentence_transformers" or name.startswith("sentence_transformers."):
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_st)

    store = BM25FAISSStore()
    docs = [
        Evidence(source="t", identifier="a", title="epoxy coating", snippet="corrosion barrier", relevance=1.0),
        Evidence(source="t", identifier="b", title="quantum qubit", snippet="superconducting", relevance=0.9),
    ]
    store.ingest(docs)
    assert store._faiss_index is None
    assert store._get_embedder() is None

    hits = store.query("epoxy corrosion coating", k=2)
    assert hits
    assert hits[0].identifier == "a"

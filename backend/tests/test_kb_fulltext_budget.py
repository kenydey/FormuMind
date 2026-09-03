"""How much of the stored full text actually reaches the model, and whether the
UI tells the truth about vectors.

Both halves are about the same failure shape: the expensive work succeeds and
something cheap downstream quietly discards or misreports it.

* A chunk is fetched, parsed, chunked and persisted at 1600 characters — then a
  hardcoded 600-char clip threw away two thirds of it on the way to the LLM.
* `embedding_available` is an import check, so it can report a healthy corpus
  while every single vector is NULL and retrieval has fallen back to token
  overlap.
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.services import kb_index


@pytest.fixture(autouse=True)
def _fresh():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ── snippet budget ───────────────────────────────────────────────────────────


def test_default_passes_the_whole_chunk_through(monkeypatch):
    monkeypatch.setattr(get_settings(), "kb_snippet_max_chars", 0, raising=False)
    text = "甲" * 1600
    assert kb_index.chunk_snippet(text) == text


def test_a_positive_limit_clips(monkeypatch):
    monkeypatch.setattr(get_settings(), "kb_snippet_max_chars", 300, raising=False)
    assert len(kb_index.chunk_snippet("甲" * 1600)) == 300


def test_short_text_is_untouched_either_way(monkeypatch):
    for limit in (0, 300):
        monkeypatch.setattr(get_settings(), "kb_snippet_max_chars", limit, raising=False)
        assert kb_index.chunk_snippet("短文本") == "短文本"


def test_fetched_evidence_is_no_longer_clipped_to_600(monkeypatch):
    """The regression this exists for: `snippet=chunk.text[:600]` in
    fulltext_fetcher discarded most of a chunk that had just been downloaded."""
    from app.domain.schemas import Evidence
    from app.services.fulltext_fetcher import _text_to_chunks

    monkeypatch.setattr(get_settings(), "kb_snippet_max_chars", 0, raising=False)
    ev = Evidence(title="专利", snippet="", source="patents", identifier="CN1", relevance=0.9)
    out = _text_to_chunks("# 标题\n\n" + "甲" * 4000, ev)

    assert out, "chunking produced nothing"
    assert len(out[0].snippet) > 600
    # And it matches the configured chunk size rather than some other cap.
    assert len(out[0].snippet) == get_settings().ingest_chunk_max_chars


def test_chunk_cap_allows_a_long_document(monkeypatch):
    """200 chunks × 1600 chars ≈ 320K was the only place a *successfully*
    fetched full text was still silently truncated."""
    assert get_settings().kb_max_chunks_per_source >= 600


# ── vector honesty ───────────────────────────────────────────────────────────


def test_empty_corpus_makes_no_claim():
    h = kb_index._vector_health(0, 0)
    assert h["vector_mode"] == "empty"
    assert h["vector_hint"] == ""


def test_vectors_present_reports_semantic():
    h = kb_index._vector_health(100, 100)
    assert h["vector_mode"] == "semantic"
    assert h["vector_hint"] == ""


def test_library_installed_but_nothing_embedded_is_flagged(monkeypatch):
    """The dangerous state, and the one the old badge rendered green.

    The library imports, so `embedding_available` says yes; the model download
    failed, so `_embed_texts` returned None for every row and retrieval is
    keyword overlap. Nothing about that is visible unless we say it.
    """
    monkeypatch.setattr(kb_index, "_embedding_probe", lambda: True)
    h = kb_index._vector_health(100, 0)
    assert h["vector_mode"] == "degraded"
    assert "没有任何切块被向量化" in h["vector_hint"]
    assert "重建索引" in h["vector_hint"]  # says what to do


def test_no_embedding_library_is_reported_as_keyword_not_a_fault(monkeypatch):
    monkeypatch.setattr(kb_index, "_embedding_probe", lambda: False)
    h = kb_index._vector_health(100, 0)
    assert h["vector_mode"] == "keyword"
    assert "sentence-transformers" in h["vector_hint"]


def test_degraded_and_keyword_are_distinguished(monkeypatch):
    """Both have zero vectors, but only one is a malfunction — conflating them
    would either cry wolf on an expected setup or hide a broken one."""
    monkeypatch.setattr(kb_index, "_embedding_probe", lambda: True)
    installed = kb_index._vector_health(50, 0)["vector_mode"]
    monkeypatch.setattr(kb_index, "_embedding_probe", lambda: False)
    absent = kb_index._vector_health(50, 0)["vector_mode"]
    assert installed != absent


def test_stats_endpoint_exposes_the_mode(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    body = TestClient(app).get("/api/kb/stats").json()
    assert body["vector_mode"] in {"semantic", "degraded", "keyword", "empty"}
    assert "rag_backend" in body
    # The effective backend, not the configured string. Must stay in step with
    # `rag.active_rag_backend`, which gained "bm25_faiss" as the CPU default and
    # "pylate" for the GPU path.
    assert body["rag_backend"] in {"pylate", "colbert", "bm25_faiss", "embedding", "tfidf"}

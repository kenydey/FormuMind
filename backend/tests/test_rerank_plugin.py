"""W3-2 (P1-21): cross-encoder rerank optional plugin.

All model/package access is mocked -- no downloads, no network.
"""
from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from app.services import rerank_plugin
from app.services.rerank_plugin import (
    CrossEncoderReranker,
    NullReranker,
    build_reranker,
    rerank_candidates,
)


def _ev(title, snippet="x", relevance=0.5):
    return SimpleNamespace(title=title, snippet=snippet, relevance=relevance)


def _cands():
    return [_ev("alpha doc"), _ev("beta doc"), _ev("gamma doc")]


def _settings(enabled=True, model=""):
    return SimpleNamespace(rerank_plugin_enabled=enabled, rerank_model=model)


def _no_st(monkeypatch):
    """Simulate sentence_transformers not installed."""
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)


def _fake_st(monkeypatch, scores=None, load_error=None):
    """Install a fake sentence_transformers module.

    scores: list returned by predict (None -> length = n candidates,
    computed from text length). load_error: exception raised by
    CrossEncoder.__init__.
    """
    fake = types.ModuleType("sentence_transformers")

    class FakeCE:
        def __init__(self, name):
            self.name = name
            if load_error is not None:
                raise load_error

        def predict(self, pairs):
            if scores is not None:
                return list(scores)
            return [float(len(p[1])) for p in pairs]

    fake.CrossEncoder = FakeCE
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    return FakeCE


# ── NullReranker ──────────────────────────────────────────────────────────


def test_null_reranker_keeps_order():
    cands = _cands()
    out, applied = NullReranker().rerank("q", cands, k=2)
    assert applied is False
    assert out == cands[:2]


# ── CrossEncoderReranker degradation ──────────────────────────────────────


def test_missing_package_degrades_to_null(monkeypatch, caplog):
    _no_st(monkeypatch)
    r = CrossEncoderReranker("some-model")
    assert r.degraded is True
    cands = _cands()
    with caplog.at_level("WARNING"):
        out, applied = r.rerank("q", cands, k=3)
    assert applied is False
    assert out == cands  # upstream order preserved
    assert any("degraded" in rec.message for rec in caplog.records)


def test_model_load_failure_degrades(monkeypatch):
    _fake_st(monkeypatch, load_error=RuntimeError("no weights"))
    r = CrossEncoderReranker("some-model")
    assert r.degraded is False  # import ok; load is lazy
    cands = _cands()
    out, applied = r.rerank("q", cands, k=3)
    assert applied is False
    assert out == cands
    assert r.degraded is True  # stays degraded afterwards


def test_predict_failure_degrades(monkeypatch):
    FakeCE = _fake_st(monkeypatch)

    def _boom(self, pairs):
        raise RuntimeError("cuda oom")

    monkeypatch.setattr(FakeCE, "predict", _boom)
    r = CrossEncoderReranker("m")
    cands = _cands()
    out, applied = r.rerank("q", cands, k=3)
    assert applied is False
    assert out == cands
    assert r.degraded is True


def test_build_reranker_degrades_on_missing_package(monkeypatch):
    _no_st(monkeypatch)
    r = build_reranker("m")
    assert isinstance(r, NullReranker)


# ── CrossEncoderReranker success path ─────────────────────────────────────


def test_cross_encoder_reorders_by_score(monkeypatch):
    _fake_st(monkeypatch, scores=[0.1, 0.9, 0.5])
    r = CrossEncoderReranker("m")
    cands = _cands()
    out, applied = r.rerank("q", cands, k=2)
    assert applied is True
    assert [c.title for c in out] == ["beta doc", "gamma doc"]


def test_cross_encoder_score_length_mismatch(monkeypatch):
    _fake_st(monkeypatch, scores=[0.1])  # 1 score vs 3 candidates
    r = CrossEncoderReranker("m")
    cands = _cands()
    out, applied = r.rerank("q", cands, k=3)
    assert applied is False
    assert out == cands


def test_cross_encoder_empty_candidates(monkeypatch):
    _fake_st(monkeypatch, scores=[])
    out, applied = CrossEncoderReranker("m").rerank("q", [], k=3)
    assert out == [] and applied is False


# ── rerank_candidates chain ───────────────────────────────────────────────


def test_chain_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(rerank_plugin, "get_settings", lambda: _settings(False))
    cands = _cands()
    out, applied = rerank_candidates("q", cands, k=3)
    assert applied == "none"
    assert out == cands


def test_chain_cross_encoder_applied(monkeypatch):
    monkeypatch.setattr(rerank_plugin, "get_settings", lambda: _settings(True))
    _fake_st(monkeypatch, scores=[0.2, 0.8, 0.4])
    cands = _cands()
    out, applied = rerank_candidates("q", cands, k=3)
    assert applied == "cross_encoder"
    assert [c.title for c in out] == ["beta doc", "gamma doc", "alpha doc"]


def test_chain_falls_back_to_llm(monkeypatch):
    monkeypatch.setattr(rerank_plugin, "get_settings", lambda: _settings(True))
    _no_st(monkeypatch)  # cross-encoder unavailable

    def _fake_llm(query, candidates, k, req=None):
        items = [
            SimpleNamespace(evidence=c, score=1.0 - i, original_index=i)
            for i, c in enumerate(reversed(candidates[:k]))
        ]
        return items, True

    monkeypatch.setattr(rerank_plugin, "_llm_fallback_scored", _fake_llm)
    cands = _cands()
    out, applied = rerank_candidates("q", cands, k=3)
    assert applied == "llm"
    assert [c.title for c in out] == ["gamma doc", "beta doc", "alpha doc"]


def test_chain_all_fail_returns_none(monkeypatch):
    monkeypatch.setattr(rerank_plugin, "get_settings", lambda: _settings(True))
    _no_st(monkeypatch)

    def _boom(query, candidates, k, req=None):
        raise RuntimeError("llm down")

    monkeypatch.setattr(rerank_plugin, "_llm_fallback_scored", _boom)
    cands = _cands()
    out, applied = rerank_candidates("q", cands, k=3)
    assert applied == "none"
    assert out == cands


def test_chain_k_over_cap_skips_plugin(monkeypatch):
    monkeypatch.setattr(rerank_plugin, "get_settings", lambda: _settings(True))
    _fake_st(monkeypatch, scores=[0.9, 0.1, 0.5])
    cands = _cands()
    out, applied = rerank_candidates("q", cands, k=51)
    assert applied == "none"
    assert out == cands  # plugin not triggered


def test_chain_empty_candidates(monkeypatch):
    monkeypatch.setattr(rerank_plugin, "get_settings", lambda: _settings(True))
    out, applied = rerank_candidates("q", [], k=3)
    assert out == [] and applied == "none"


# ── rag.py wiring ─────────────────────────────────────────────────────────


def test_llm_rerank_routes_to_plugin_when_enabled(monkeypatch):
    from app.services import rag

    monkeypatch.setattr(
        "app.config.get_settings", lambda: _settings(True)
    )
    called = {}

    def _fake_chain(query, candidates, k=6, req=None):
        called["k"] = k
        return list(reversed(candidates[:k])), "cross_encoder"

    monkeypatch.setattr(rerank_plugin, "rerank_candidates", _fake_chain)
    cands = _cands()
    out = rag.llm_rerank("q", cands, k=3)
    assert called["k"] == 3
    assert [c.title for c in out] == ["gamma doc", "beta doc", "alpha doc"]


def test_llm_rerank_skips_plugin_when_disabled(monkeypatch):
    from app.services import rag

    monkeypatch.setattr(
        "app.config.get_settings", lambda: _settings(False)
    )

    def _boom(query, candidates, k=6, req=None):  # must not be called
        raise AssertionError("plugin chain must not trigger")

    monkeypatch.setattr(rerank_plugin, "rerank_candidates", _boom)

    def _fake_scored(query, candidates, k=6, req=None):
        items = [
            SimpleNamespace(evidence=c, score=0.5, original_index=i)
            for i, c in enumerate(candidates[:k])
        ]
        return items, False

    monkeypatch.setattr(rag, "llm_rerank_scored", _fake_scored)
    out = rag.llm_rerank("q", _cands(), k=2)
    assert [c.title for c in out] == ["alpha doc", "beta doc"]


def test_llm_rerank_skips_plugin_when_k_over_cap(monkeypatch):
    from app.services import rag

    monkeypatch.setattr(
        "app.config.get_settings", lambda: _settings(True)
    )

    def _boom(query, candidates, k=6, req=None):
        raise AssertionError("plugin chain must not trigger for k>50")

    monkeypatch.setattr(rerank_plugin, "rerank_candidates", _boom)

    def _fake_scored(query, candidates, k=6, req=None):
        items = [
            SimpleNamespace(evidence=c, score=0.5, original_index=i)
            for i, c in enumerate(candidates[:k])
        ]
        return items, True

    monkeypatch.setattr(rag, "llm_rerank_scored", _fake_scored)
    out = rag.llm_rerank("q", _cands(), k=60)
    assert len(out) == 3


def test_llm_rerank_plugin_crash_falls_back(monkeypatch):
    from app.services import rag

    monkeypatch.setattr(
        "app.config.get_settings", lambda: _settings(True)
    )

    def _crash(query, candidates, k=6, req=None):
        raise RuntimeError("plugin exploded")

    monkeypatch.setattr(rerank_plugin, "rerank_candidates", _crash)

    def _fake_scored(query, candidates, k=6, req=None):
        items = [
            SimpleNamespace(evidence=c, score=0.5, original_index=i)
            for i, c in enumerate(candidates[:k])
        ]
        return items, True

    monkeypatch.setattr(rag, "llm_rerank_scored", _fake_scored)
    out = rag.llm_rerank("q", _cands(), k=2)
    assert [c.title for c in out] == ["alpha doc", "beta doc"]

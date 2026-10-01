"""C-6 rerank A/B framework tests.

MECHANICS ONLY: run_golden_eval is stubbed with synthetic numbers to verify
the comparison math and the decision rule. These tests say NOTHING about
rerank quality on the real chemistry corpus — the real A/B needs real
embeddings (blocked: dev DB has 0 embeddings, sandbox cannot fetch models).
"""
from __future__ import annotations

import pytest

from app.services import rerank_ab
from app.services.rerank_ab import (
    RERANK_NDCG_FLIP_THRESHOLD,
    rerank_default_decision,
    run_rerank_ab,
)


def _stub_golden_eval(mode, **kw):
    base = {"total": 20, "recall_at_k": 0.80, "mrr": 0.60, "ndcg_at_k": 0.70}
    if mode == "hybrid_rerank":
        base.update({"recall_at_k": 0.85, "mrr": 0.66, "ndcg_at_k": 0.75})
    return base


def test_ab_report_math(monkeypatch):
    monkeypatch.setattr(
        "app.services.kb_query_test.run_golden_eval", _stub_golden_eval
    )
    report = run_rerank_ab(top_k=6)
    assert report["control"]["mode"] == "hybrid"
    assert report["treatment"]["mode"] == "hybrid_rerank"
    assert report["delta_ndcg_at_k"] == pytest.approx(0.05)
    assert report["delta_recall_at_k"] == pytest.approx(0.05)
    assert report["delta_mrr"] == pytest.approx(0.06)


def test_decision_enables_above_threshold():
    out = rerank_default_decision({"delta_ndcg_at_k": RERANK_NDCG_FLIP_THRESHOLD + 0.01})
    assert out["enable_by_default"] is True
    assert "ON" in out["reason"]


def test_decision_stays_off_at_or_below_threshold():
    for d in (RERANK_NDCG_FLIP_THRESHOLD, 0.0, -0.05):
        out = rerank_default_decision({"delta_ndcg_at_k": d})
        assert out["enable_by_default"] is False
        assert "OFF" in out["reason"]


def test_decision_handles_missing_delta():
    out = rerank_default_decision({})
    assert out["enable_by_default"] is False


def test_ab_propagates_top_k(monkeypatch):
    seen = {}

    def _stub(mode, **kw):
        seen[mode] = kw.get("top_k")
        return {"total": 1, "recall_at_k": 0.0, "mrr": 0.0, "ndcg_at_k": 0.0}

    monkeypatch.setattr("app.services.kb_query_test.run_golden_eval", _stub)
    run_rerank_ab(top_k=10)
    assert seen == {"hybrid": 10, "hybrid_rerank": 10}

"""Tests for search-stream LLM rerank (Phase B R-2a)."""
from __future__ import annotations

from app.domain.schemas import Evidence, ProductDomain, Requirement
from app.services.rag import _rerank_query, llm_rerank


def _ev(i: int) -> Evidence:
    return Evidence(
        source="patent",
        identifier=f"P{i}",
        title=f"Hit {i}",
        snippet=f"snippet {i}",
        relevance=0.5,
    )


def test_rerank_query_includes_requirement_context():
    req = Requirement(
        project_id="p1",
        product_type="防腐蚀环氧底漆",
        application="carbon_steel",
        domain=ProductDomain.anticorrosion_coating,
        substrate="carbon_steel",
    )
    q = _rerank_query("zinc phosphate", req)
    assert "防腐蚀环氧底漆" in q
    assert "carbon_steel" in q


def test_llm_rerank_reorders_by_mock_scores(monkeypatch):
    candidates = [_ev(0), _ev(1), _ev(2)]

    def fake_json(prompt):
        return {"scores": [{"i": 2, "score": 0.99}, {"i": 0, "score": 0.5}, {"i": 1, "score": 0.1}]}

    monkeypatch.setattr("app.services.llm.complete_json", fake_json)
    out = llm_rerank("zinc", candidates, k=2)
    assert [e.identifier for e in out] == ["P2", "P0"]

"""P1-4: ``kb_only`` (and the degraded-hybrid path) must actually recommend.

Before, retrieved evidence snippets were turned into component-less
``RecommendedFormula`` objects, which the orchestration rejects one by one
(``recommended_to_formulation`` needs components). Result: with evidence the
endpoint answered 200 with ZERO formulas plus a "has no components" warning per
document — the UI then showed the first warning ("kb_only 模式：仅返回…") as an
error — and with an empty retrieval it answered a bare 503. The degraded-hybrid
branch ("LLM failed, fall back to KB") had the same hole.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.domain.schemas import Evidence, ObjectiveSpec, ProductDomain, Requirement
from app.main import app


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{tmp_path}/recommend.db")
    get_settings.cache_clear()
    yield TestClient(app)
    get_settings.cache_clear()


def _set_mode(monkeypatch, mode: str) -> None:
    monkeypatch.setenv("FORMUMIND_FORMULATION_MODE", mode)
    get_settings.cache_clear()


def _retrieval(monkeypatch, evidence: list[Evidence] | Exception) -> None:
    import app.pipeline.research_graph as rg

    def fake(*_a, **_k):
        if isinstance(evidence, Exception):
            raise evidence
        return SimpleNamespace(grounded_evidence=evidence)

    monkeypatch.setattr(rg, "resolve_grounded_evidence", fake)


def _body() -> dict:
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        objectives=[
            ObjectiveSpec(metric="salt_spray_hours", weight=0.6, direction="maximize"),
            ObjectiveSpec(metric="cost_cny_per_kg", weight=0.4, direction="minimize"),
        ],
    )
    return {"requirement": req.model_dump(), "n": 3}


def _ev() -> Evidence:
    return Evidence(
        source="seed", identifier="kb:1", title="无铬钝化", snippet="锆盐钝化膜…", relevance=0.9
    )


def test_kb_only_with_evidence_returns_formulations(client, monkeypatch):
    _set_mode(monkeypatch, "kb_only")
    _retrieval(monkeypatch, [_ev()])

    res = client.post("/api/formulations/recommend", json=_body())

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["engine"] == "offline"
    assert len(body["formulas"]) >= 1 and len(body["scored"]) >= 1
    assert body["returned_n"] == len(body["scored"])
    # Real formulations (components), not evidence stubs.
    assert body["formulas"][0]["components"]
    # The first warning is what the UI shows when a run yields nothing — it must
    # now describe the mode, and no per-document "has no components" noise.
    assert body["warnings"][0].startswith("kb_only 模式：未调用 LLM")
    assert not any("has no components" in w for w in body["warnings"])
    assert any("1 条知识库证据" in w for w in body["warnings"])


def test_kb_only_with_empty_retrieval_is_not_a_503(client, monkeypatch):
    _set_mode(monkeypatch, "kb_only")
    _retrieval(monkeypatch, [])

    res = client.post("/api/formulations/recommend", json=_body())

    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["scored"]) >= 1
    assert any("未检索到匹配证据" in w for w in body["warnings"])


def test_kb_only_retrieval_failure_is_still_a_503(client, monkeypatch):
    _set_mode(monkeypatch, "kb_only")
    _retrieval(monkeypatch, RuntimeError("index offline"))

    res = client.post("/api/formulations/recommend", json=_body())

    assert res.status_code == 503
    assert "知识库检索失败" in res.json()["detail"]


def test_hybrid_llm_failure_with_evidence_degrades_to_real_candidates(client, monkeypatch):
    import app.api.formulations as fm

    _set_mode(monkeypatch, "hybrid")
    _retrieval(monkeypatch, [_ev()])

    def boom(*_a, **_k):
        raise RuntimeError("LLM upstream 500")

    monkeypatch.setattr(fm.llm, "recommend_formulations", boom)

    res = client.post("/api/formulations/recommend", json=_body())

    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["scored"]) >= 1 and body["formulas"][0]["components"]
    assert any("LLM 合成失败" in w for w in body["warnings"])


def test_hybrid_llm_failure_without_evidence_is_still_a_503(client, monkeypatch):
    import app.api.formulations as fm

    _set_mode(monkeypatch, "hybrid")
    _retrieval(monkeypatch, [])

    def boom(*_a, **_k):
        raise RuntimeError("LLM upstream 500")

    monkeypatch.setattr(fm.llm, "recommend_formulations", boom)

    res = client.post("/api/formulations/recommend", json=_body())

    assert res.status_code == 503


def test_offline_response_helper_states_whether_evidence_was_used():
    import app.api.formulations as fm

    req = Requirement(domain=ProductDomain.anticorrosion_coating)
    with_ev = fm._offline_recommendation_response(req, [_ev()], n=3, note="N")
    without = fm._offline_recommendation_response(req, [], n=3, note="N")

    assert with_ev.formulas and without.formulas
    assert with_ev.warnings[0] == "N" and "1 条知识库证据" in with_ev.warnings[1]
    assert "未检索到匹配证据" in without.warnings[1]

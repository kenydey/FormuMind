"""Consumers of ``RecommendFormulationsResponse`` read fields that exist.

Two silent failures came from reading names the response never had, both hidden
by tests whose fakes invented the missing fields (``SimpleNamespace(formulations
=...)``, ``{"grounded_evidence": []}``):

* ``doe_cycle_service.build_candidate_formulations`` read ``.formulations``;
  the AttributeError was swallowed, so every DOE cycle spent a full recommend
  call and then used the single baseline formulation — the Top-12 never counted.
* ``run_recommend_task`` read ``grounded_evidence``; the response had no such
  field, so ``research.evidence`` was always ``[]`` and nothing was ever handed
  to the KB-ingest dispatcher.

These tests use the real response model instead of fakes.
"""
from __future__ import annotations

import types
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.domain.schemas import (
    Evidence,
    Formulation,
    Ingredient,
    ObjectiveSpec,
    ProductDomain,
    Requirement,
)
from app.main import app


def _form(name: str) -> Formulation:
    return Formulation(
        name=name,
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[Ingredient(name=f"树脂-{name}", role="resin", weight_pct=100.0)],
    )


def _ev(ident: str) -> Evidence:
    return Evidence(source="seed", identifier=ident, title=ident, snippet="锆盐钝化膜…", relevance=0.9)


def _req() -> Requirement:
    return Requirement(
        domain=ProductDomain.anticorrosion_coating,
        objectives=[ObjectiveSpec(metric="salt_spray_hours", weight=1.0, direction="maximize")],
    )


# ── DOE cycle candidates ────────────────────────────────────────────────────


def _patch_recommend(monkeypatch, response):
    import app.api.formulations as form_mod

    monkeypatch.setattr(form_mod, "recommend_formulations", lambda body: response)


def test_candidates_come_from_scored_not_a_missing_attribute(monkeypatch):
    from app.api.formulations import RecommendFormulationsResponse
    from app.services.doe_cycle_service import build_candidate_formulations

    scored = [_form("a"), _form("b"), _form("c")]
    _patch_recommend(
        monkeypatch, RecommendFormulationsResponse(formulas=[], engine="llm", scored=scored)
    )

    out = build_candidate_formulations(_req())

    assert [f.name for f in out] == ["a", "b", "c"], "must not fall back to the baseline"


def test_empty_scored_falls_back_to_baseline_instead_of_no_candidates(monkeypatch):
    from app.api.formulations import RecommendFormulationsResponse
    from app.services.doe_cycle_service import build_candidate_formulations

    _patch_recommend(
        monkeypatch, RecommendFormulationsResponse(formulas=[], engine="llm", scored=[])
    )

    out = build_candidate_formulations(_req())

    assert len(out) == 1, "fail-open contract: one baseline formulation"


def test_real_response_model_has_no_formulations_field():
    """Pins the shape the old code assumed; if someone adds it, delete the guard."""
    from app.api.formulations import RecommendFormulationsResponse

    assert "formulations" not in RecommendFormulationsResponse.model_fields
    assert {"formulas", "scored", "grounded_evidence"} <= set(
        RecommendFormulationsResponse.model_fields
    )


# ── evidence in the response and through the async task ─────────────────────


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{tmp_path}/recwire.db")
    monkeypatch.setenv("FORMUMIND_FORMULATION_MODE", "kb_only")
    get_settings.cache_clear()
    yield TestClient(app)
    get_settings.cache_clear()


def _retrieval(monkeypatch, evidence):
    import app.pipeline.research_graph as rg

    monkeypatch.setattr(
        rg, "resolve_grounded_evidence", lambda *a, **k: SimpleNamespace(grounded_evidence=evidence)
    )


def test_endpoint_returns_the_evidence_it_was_grounded_on(client, monkeypatch):
    _retrieval(monkeypatch, [_ev("kb:1"), _ev("kb:2")])

    res = client.post(
        "/api/formulations/recommend", json={"requirement": _req().model_dump(), "n": 2}
    )

    assert res.status_code == 200, res.text
    assert [e["identifier"] for e in res.json()["grounded_evidence"]] == ["kb:1", "kb:2"]


def test_endpoint_echoes_caller_sources_when_retrieval_is_empty(client, monkeypatch):
    _retrieval(monkeypatch, [])
    mine = _ev("user:1")

    res = client.post(
        "/api/formulations/recommend",
        json={"requirement": _req().model_dump(), "n": 2, "sources": [mine.model_dump()]},
    )

    assert res.status_code == 200, res.text
    assert [e["identifier"] for e in res.json()["grounded_evidence"]] == ["user:1"]


class _FakeTracker:
    def __init__(self, *a, **k):
        pass

    def emit(self, *a, **k):
        pass

    def thought(self, *a, **k):
        pass

    def finish(self, *a, **k):
        pass


def test_async_task_forwards_evidence_and_dispatches_kb_ingest(monkeypatch):
    from app.api.formulations import RecommendFormulationsResponse
    from app.worker import tasks as wt

    evidence = [_ev("kb:9")]
    resp = RecommendFormulationsResponse(
        formulas=[], engine="llm", scored=[], grounded_evidence=evidence, recommend_id="r-1"
    )
    monkeypatch.setattr("app.api.formulations.recommend_formulations", lambda body: resp)
    monkeypatch.setattr(wt, "ThinkingTracker", _FakeTracker)
    monkeypatch.setattr(wt, "persist_result", lambda *a, **k: None)
    monkeypatch.setattr(wt, "_persist_terminal", lambda *a, **k: None)
    ingested: list = []
    monkeypatch.setattr(wt, "dispatch_kb_ingest", lambda ev, **k: ingested.append(ev) or "kb-1")

    res = wt.run_recommend_task.apply(
        args=({"requirement": {"domain": "anticorrosion_coating"}, "n": 2, "sources": []},),
        task_id="task-evidence",
    )
    research = res.get()["research"]

    assert [e["identifier"] for e in research["evidence"]] == ["kb:9"]
    assert len(ingested) == 1 and [e["identifier"] for e in ingested[0]] == ["kb:9"]
    assert research["recommend_id"] == "r-1"

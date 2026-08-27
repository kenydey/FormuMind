"""Tests for third-priority verification DOE generation in trade-off analysis."""

from __future__ import annotations

import pytest

from app.domain.schemas import (
    DOEPlan,
    DOEFactor,
    Formulation,
    Ingredient,
    ObjectiveSpec,
    ProductDomain,
    Requirement,
)
from app.domain.tradeoff_schemas import TradeOffAnalysis
from app.services.tradeoff_analysis import analyze_tradeoffs


def _req() -> Requirement:
    return Requirement(
        domain="anticorrosion_coating",
        substrate="carbon_steel",
        objectives=[ObjectiveSpec(metric="corrosion_resistance", direction="maximize")],
    )


def _form(name: str, score: float, kg_infeasible: bool = False) -> Formulation:
    f = Formulation(
        name=name,
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[Ingredient(name=n, role="resin", weight_pct=50.0) for n in ("环氧树脂", "固化剂A")],
    )
    f.score = score
    f.predicted = {"corrosion_resistance": score * 100, "cost_cny_per_kg": 10.0}
    if kg_infeasible:
        f.kg_compat = {"feasible": False, "status": "infeasible", "incompatible_pairs": [], "synergy_pairs": [], "reasons": ["x"]}
    return f


def _fake_doe_plan() -> DOEPlan:
    return DOEPlan(
        design="lhs",
        factors=[DOEFactor(name="cure_temp", low=100, high=200)],
        runs=[],
    )


def test_verification_doe_generated_for_frontier(monkeypatch):
    """Pareto-front candidates get a verification DOE when enabled."""

    class _S:
        recommend_tradeoff_enabled = True
        verification_doe_enabled = True
        verification_doe_n = 4
        recommend_uncertainty_flag = True

    monkeypatch.setattr("app.services.tradeoff_analysis.get_settings", lambda: _S())
    monkeypatch.setattr(
        "app.pipeline.workflow.build_doe",
        lambda req, design, n: _fake_doe_plan(),
    )

    forms = [_form("A", 0.9), _form("B", 0.6), _form("C", 0.4)]
    # A dominates → Pareto front; others not.
    ta: TradeOffAnalysis = analyze_tradeoffs(
        forms, _req().objectives, req=_req(), settings=_S()
    )
    assert ta is not None
    # At least the Pareto-front candidate A should have a verification DOE.
    assert len(ta.verification_does) >= 1
    v = ta.verification_does[0]
    assert v.candidate_name
    assert v.doe_plan is not None
    assert v.doe_plan.design == "verification"


def test_kg_infeasible_skipped(monkeypatch):
    """KG-incompatible candidates are NOT given a verification DOE."""

    class _S:
        recommend_tradeoff_enabled = True
        verification_doe_enabled = True
        verification_doe_n = 4
        recommend_uncertainty_flag = True

    monkeypatch.setattr("app.services.tradeoff_analysis.get_settings", lambda: _S())
    monkeypatch.setattr(
        "app.pipeline.workflow.build_doe",
        lambda req, design, n: _fake_doe_plan(),
    )

    # Only one candidate, and it's KG-infeasible → no verification DOE.
    forms = [_form("Bad", 0.9, kg_infeasible=True)]
    ta = analyze_tradeoffs(forms, _req().objectives, req=_req(), settings=_S())
    assert ta is not None
    assert ta.verification_does == []


def test_disabled_yields_empty(monkeypatch):
    """verification_doe_enabled=False → no verification DOE."""

    class _S:
        recommend_tradeoff_enabled = True
        verification_doe_enabled = False
        verification_doe_n = 4
        recommend_uncertainty_flag = True

    monkeypatch.setattr("app.services.tradeoff_analysis.get_settings", lambda: _S())
    forms = [_form("A", 0.9), _form("B", 0.6)]
    ta = analyze_tradeoffs(forms, _req().objectives, req=_req(), settings=_S())
    assert ta is not None
    assert ta.verification_does == []

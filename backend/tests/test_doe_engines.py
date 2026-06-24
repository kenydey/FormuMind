"""Tests for optional pydoe DOE engine."""
from __future__ import annotations

import pytest

from app.domain.schemas import DOEFactor
from app.services.engines.doe_registry import build_doe_plan, pydoe_available, resolve_doe_engine


FACTORS = [
    DOEFactor(name="A", low=1.0, high=10.0, unit="wt%"),
    DOEFactor(name="B", low=2.0, high=8.0, unit="wt%"),
]


def test_resolve_doe_engine_native_when_pydoe_missing(monkeypatch):
    monkeypatch.setattr("app.services.engines.doe_registry.pydoe_available", lambda: False)
    assert resolve_doe_engine("auto", "lhs") == "native"
    assert resolve_doe_engine("pydoe", "lhs") == "native"


def test_native_lhs_plan_always_works():
    plan = build_doe_plan(FACTORS, "lhs", engine="native", n=6)
    assert len(plan.runs) == 6
    assert plan.factors == FACTORS
    assert "engine=native" in plan.notes


@pytest.mark.skipif(not pydoe_available(), reason="pydoe not installed")
def test_pydoe_lhs_plan():
    plan = build_doe_plan(FACTORS, "lhs", engine="pydoe", n=8)
    assert len(plan.runs) == 8
    assert "engine=pydoe" in plan.notes
    for run in plan.runs:
        for f in FACTORS:
            assert f.low <= run.natural[f.name] <= f.high


def test_pydoe_design_falls_back_to_native(monkeypatch):
    monkeypatch.setattr("app.services.engines.pydoe_engine.pydoe_available", lambda: True)

    def boom(*args, **kwargs):
        raise RuntimeError("simulated pydoe failure")

    monkeypatch.setattr("app.services.engines.pydoe_engine.build_pydoe_plan", boom)
    plan = build_doe_plan(FACTORS, "lhs", engine="pydoe", n=5)
    assert "fallback" in plan.notes or "engine=native" in plan.notes

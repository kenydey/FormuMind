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


@pytest.mark.skipif(not pydoe_available(), reason="pydoe not installed")
def test_pydoe_lhs_explores_full_factor_range():
    """LHS/sobol are already unit-scaled ([0, 1]); they must not be run through
    the coded (v + 1) / 2 remap meant for +-1-scaled designs like ccd/bbdesign,
    which used to collapse every factor into its upper half."""
    plan = build_doe_plan(FACTORS, "lhs", engine="pydoe", n=200)
    for f in FACTORS:
        span = f.high - f.low
        naturals = [run.natural[f.name] for run in plan.runs]
        assert min(naturals) <= f.low + 0.25 * span
        assert max(naturals) >= f.high - 0.25 * span


def test_pydoe_design_falls_back_to_native(monkeypatch):
    monkeypatch.setattr("app.services.engines.pydoe_engine.pydoe_available", lambda: True)

    def boom(*args, **kwargs):
        raise RuntimeError("simulated pydoe failure")

    monkeypatch.setattr("app.services.engines.pydoe_engine.build_pydoe_plan", boom)
    plan = build_doe_plan(FACTORS, "lhs", engine="pydoe", n=5)
    assert "fallback" in plan.notes or "engine=native" in plan.notes


def test_mixture_design_failure_raises_not_fallback(monkeypatch):
    """P0: simplex_lattice 失败必须显式报错, 不允许静默降级无约束 LHS。"""
    monkeypatch.setattr("app.services.engines.pydoe_engine.pydoe_available", lambda: True)

    def boom(*args, **kwargs):
        raise RuntimeError("simulated pydoe simplex failure")

    monkeypatch.setattr("app.services.engines.pydoe_engine.build_pydoe_plan", boom)
    with pytest.raises(ValueError, match="混料"):
        build_doe_plan(FACTORS, "simplex_lattice", engine="pydoe", n=6)


def test_simplex_lattice_mapping_preserves_mixture_sum():
    """Mixture proportions map onto the recipe components so that they add up to the baseline total
    (Σ midpoint of the ranges) with every value inside its own range."""
    import numpy as np

    from app.services.engines.adapters.doe_adapter import matrix_to_doe_plan

    factors = [
        DOEFactor(name="A", low=0.0, high=40.0),
        DOEFactor(name="B", low=0.0, high=30.0),
        DOEFactor(name="C", low=0.0, high=30.0),
    ]
    # One simplex row: proportions sum to 1.
    matrix = np.array([[0.5, 0.25, 0.25]], dtype=float)
    plan = matrix_to_doe_plan(matrix, factors, "simplex_lattice", engine="test")
    assert len(plan.runs) == 1
    total = sum((f.low + f.high) / 2 for f in factors)  # 50: the baseline mass of the components
    nat = plan.runs[0].natural
    assert sum(nat.values()) == pytest.approx(total, abs=1e-3)
    assert nat["A"] == pytest.approx(0.5 * total, abs=1e-3)
    assert all(f.low <= nat[f.name] <= f.high for f in factors)


def test_kg_gate_applies_to_native_engine(monkeypatch):
    """P1-5：KG 化学相容性门对 native 引擎同样生效，不再依赖 pydoe。"""
    from app.services.engines import doe_registry as reg

    class _Chk:
        feasible = False
        reasons = ["A 与 B 不相容（INHIBITS）"]

    # mock KG 检查返回不相容
    monkeypatch.setattr(
        "app.services.kg_chemical_check.check_formulation_chemistry",
        lambda *a, **k: _Chk(),
    )
    # mock skeleton（避免依赖真实 KG 数据）
    monkeypatch.setattr(
        "app.domain.knowledge.baseline_formulation",
        lambda req: object(),
    )

    class _Req:
        active_formulation = None

    plan = build_doe_plan(FACTORS, "lhs", engine="native", n=4, requirement=_Req())
    assert len(plan.runs) == 4
    assert all(r.infeasible for r in plan.runs), "native 引擎也应标记 infeasible"
    assert all("不相容" in (r.infeasible_reason or "") for r in plan.runs)

"""pyDOE KG chemical gate must actually run (not silently no-op on bad import)."""
from __future__ import annotations

from types import SimpleNamespace

from app.domain.schemas import DOEFactor
from app.services.engines import pydoe_engine as mod


FACTORS = [
    DOEFactor(name="resin", low=40.0, high=70.0, unit="wt%"),
    DOEFactor(name="hardener", low=10.0, high=30.0, unit="wt%"),
]


class _Infeasible:
    feasible = False
    reasons = ["树脂 与 固化剂 不相容：实测析晶"]


def test_pydoe_kg_gate_marks_runs_infeasible(monkeypatch):
    """Requirement + infeasible KG check → every DOE run flagged."""
    monkeypatch.setattr(mod, "pydoe_available", lambda: True)

    def fake_matrix(design, k, n):
        import numpy as np

        return np.array([[0.0, 0.0], [1.0, 1.0], [0.5, 0.5]])

    monkeypatch.setattr(mod, "_generate_matrix", fake_matrix)

    def fake_plan(matrix, factors, design, engine="pydoe"):
        return SimpleNamespace(
            runs=[
                SimpleNamespace(infeasible=False, infeasible_reason=None),
                SimpleNamespace(infeasible=False, infeasible_reason=None),
                SimpleNamespace(infeasible=False, infeasible_reason=None),
            ],
            notes="",
        )

    monkeypatch.setattr(mod, "matrix_to_doe_plan", fake_plan)

    import app.services.kg_chemical_check as kg
    import app.domain.knowledge as knowledge

    monkeypatch.setattr(
        kg, "check_formulation_chemistry", lambda *a, **k: _Infeasible()
    )
    monkeypatch.setattr(
        knowledge,
        "baseline_formulation",
        lambda req: SimpleNamespace(ingredients=[SimpleNamespace(name="resin")]),
    )

    req = SimpleNamespace(active_formulation=None)
    plan = mod.build_pydoe_plan(FACTORS, "lhs", n=3, requirement=req)

    assert all(r.infeasible for r in plan.runs)
    assert all("不相容" in (r.infeasible_reason or "") for r in plan.runs)


def test_pydoe_kg_gate_import_path_is_services_local():
    """Regression: ``from ..services.kg_…`` resolved to app.services.services."""
    import inspect
    import textwrap

    src = textwrap.dedent(inspect.getsource(mod.build_pydoe_plan))
    assert "from ..kg_chemical_check import" in src
    assert "from ..services.kg_chemical_check" not in src


def test_build_doe_plan_forwards_requirement(monkeypatch):
    from app.services.engines import doe_registry as reg

    seen: dict = {}

    def fake_fallback(factors, design, n=None, requirement=None):
        seen["requirement"] = requirement
        return SimpleNamespace(runs=[], notes="")

    monkeypatch.setattr(reg, "resolve_doe_engine", lambda engine, design: "pydoe")
    monkeypatch.setattr(reg, "build_plan_with_fallback", fake_fallback)

    req = SimpleNamespace(domain="coating")
    reg.build_doe_plan(FACTORS, "lhs", engine="pydoe", requirement=req)
    assert seen["requirement"] is req

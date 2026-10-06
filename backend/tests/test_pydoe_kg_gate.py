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
    """Requirement + infeasible KG check → every DOE run flagged.

    v13-5: 门已统一到 doe_registry.apply_kg_chemical_gate，pydoe 内联门删除。
    """
    from app.services.engines import doe_registry as reg

    monkeypatch.setattr(reg, "resolve_doe_engine", lambda engine, design: "pydoe")

    def fake_fallback(factors, design, n=None, requirement=None, seed=None):
        return SimpleNamespace(
            runs=[
                SimpleNamespace(infeasible=False, infeasible_reason=None),
                SimpleNamespace(infeasible=False, infeasible_reason=None),
                SimpleNamespace(infeasible=False, infeasible_reason=None),
            ],
            notes="",
        )

    monkeypatch.setattr(reg, "build_plan_with_fallback", fake_fallback)

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
    plan = reg.build_doe_plan(FACTORS, "lhs", engine="pydoe", n=3, requirement=req)

    assert all(r.infeasible for r in plan.runs)
    assert all("不相容" in (r.infeasible_reason or "") for r in plan.runs)


def test_pydoe_kg_gate_not_duplicated():
    """v13-5: pydoe 内联门已删，门只在 doe_registry 执行一次（不双执行）。"""
    import inspect
    import textwrap

    src = textwrap.dedent(inspect.getsource(mod.build_pydoe_plan))
    assert "check_formulation_chemistry" not in src, "内联门应已删除"
    # 门在 registry 统一执行
    from app.services.engines import doe_registry as reg

    rsrc = textwrap.dedent(inspect.getsource(reg.apply_kg_chemical_gate))
    assert "check_formulation_chemistry" in rsrc


def test_build_doe_plan_forwards_requirement(monkeypatch):
    from app.services.engines import doe_registry as reg

    seen: dict = {}

    def fake_fallback(factors, design, n=None, requirement=None, seed=None):
        seen["requirement"] = requirement
        return SimpleNamespace(runs=[], notes="")

    monkeypatch.setattr(reg, "resolve_doe_engine", lambda engine, design: "pydoe")
    monkeypatch.setattr(reg, "build_plan_with_fallback", fake_fallback)

    req = SimpleNamespace(domain="coating")
    reg.build_doe_plan(FACTORS, "lhs", engine="pydoe", requirement=req)
    assert seen["requirement"] is req

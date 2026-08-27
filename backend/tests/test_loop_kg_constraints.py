"""Tests for KG chemical-feasibility wiring through loop result objects."""

from __future__ import annotations

from app.domain.schemas import (
    ActiveDoeResult,
    BaybeRecommendResult,
    DOEPlan,
    DOERun,
    LoopReport,
    OptimizationResult,
)


def _plan(n_runs: int = 3) -> DOEPlan:
    return DOEPlan(
        design="lhs",
        factors=[],
        runs=[DOERun(run_id=i, coded={}, natural={}) for i in range(n_runs)],
    )


def test_baybe_result_carries_chemical_verdict():
    plan = _plan()
    verdict = {"feasible": False, "status": "infeasible", "reasons": ["A 与 B 不相容"]}
    res = BaybeRecommendResult(
        plan=plan, campaign_state="{}", engine="baybe", chemical_feasibility=verdict
    )
    assert res.chemical_feasibility == verdict
    # Gate marks every run infeasible when the shared skeleton is incompatible.
    for r in res.plan.runs:
        assert r.infeasible is False  # marks applied by engine, not by schema default


def test_active_doe_result_carries_chemical_verdict():
    verdict = {"feasible": True, "status": "pass", "reasons": []}
    res = ActiveDoeResult(plan=_plan(), engine="baybe", chemical_feasibility=verdict)
    assert res.chemical_feasibility == verdict


def test_loop_report_carries_chemical_verdict_and_message():
    verdict = {"feasible": False, "status": "infeasible", "reasons": ["A 与 B 不相容"]}
    opt = OptimizationResult(
        iterations=1,
        objective="x",
        history=[],
        top_formulations=[],
        engine="numpy-ucb",
    )
    report = LoopReport(
        domain="anticorrosion_coating",
        total_records=0,
        optimization=opt,
        next_doe=_plan(),
        engine="baybe",
        converged=False,
        loop_message=(
            "⚠ 知识图谱检测到推荐配方骨架存在材料不相容，详见化学可行性字段"
            if not verdict["feasible"]
            else ""
        ),
        chemical_feasibility=verdict,
    )
    assert report.chemical_feasibility == verdict
    assert "不相容" in report.loop_message


def test_doe_run_infeasible_flag_roundtrip():
    run = DOERun(
        run_id=0,
        coded={},
        natural={},
        infeasible=True,
        infeasible_reason="知识图谱检测到材料不相容",
    )
    assert run.infeasible is True
    assert "不相容" in run.infeasible_reason

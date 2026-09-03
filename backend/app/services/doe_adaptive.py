"""Orchestrate adaptive DOE metadata (explanations, anomalies, constraints)."""
from __future__ import annotations

from ..domain.schemas import (
    ActiveDoeResult,
    BaybeRecommendResult,
    DOEPlan,
    ExperimentRecord,
    Requirement,
)
from .doe_anomaly import detect_anomalies
from .doe_explain import (
    build_run_explanations,
    infer_strategy,
    legacy_acquisition_scores,
    recommend_next_action,
)


def _run_has_constraint_warnings(req: Requirement, run) -> list[str]:
    from ..domain.formulation_gate import validate_formulations
    from ..pipeline import reconstruct

    try:
        form = reconstruct.formulation_from_factors(req, run.natural)
        form.name = f"DOE run {run.run_id}"
        _, warnings = validate_formulations([form], req=req)
        return warnings[:3]
    except Exception:
        return []


def _constraint_warnings_for_runs(
    req: Requirement,
    plan: DOEPlan,
) -> dict[int, list[str]]:
    """Post-recommend formulation validation (constraint propagation lite)."""
    warnings_by_run: dict[int, list[str]] = {}
    suggested = [r for r in plan.runs if r.ai_suggested] or plan.runs
    for run in suggested:
        warnings = _run_has_constraint_warnings(req, run)
        if warnings:
            warnings_by_run[run.run_id] = warnings
    return warnings_by_run


def resample_plan_for_constraints(req: Requirement, plan: DOEPlan, *, max_rounds: int = 2) -> DOEPlan:
    """Swap AI-suggested runs that fail gate validation with cleaner alternates."""
    from ..domain.schemas import DOERun

    current = plan
    for _ in range(max_rounds):
        warnings = _constraint_warnings_for_runs(req, current)
        bad_ids = set(warnings.keys())
        if not bad_ids:
            break

        suggested_ids = {r.run_id for r in current.runs if r.ai_suggested}
        bad_suggested = bad_ids & suggested_ids
        if not bad_suggested:
            break

        alternates = [
            r
            for r in current.runs
            if r.run_id not in suggested_ids and not _run_has_constraint_warnings(req, r)
        ]
        if not alternates:
            break

        alt_iter = iter(alternates)
        new_runs: list[DOERun] = []
        for run in current.runs:
            if run.run_id in bad_suggested:
                try:
                    replacement = next(alt_iter)
                except StopIteration:
                    new_runs.append(run)
                    continue
                new_runs.append(
                    DOERun(
                        run_id=run.run_id,
                        coded=replacement.coded,
                        natural=replacement.natural,
                        ai_suggested=True,
                    )
                )
            else:
                new_runs.append(run)
        note = current.notes
        if bad_suggested:
            note = f"{note} | 约束重采样：替换 {len(bad_suggested)} 个不合格 AI 点".strip(" |")
        current = current.model_copy(update={"runs": new_runs, "notes": note})
    return current


def build_adaptive_metadata(
    req: Requirement,
    plan: DOEPlan,
    existing: list[ExperimentRecord],
    *,
    budget_remaining: int | None = None,
    acquisition_scores: dict[int, float] | None = None,
) -> dict:
    n_completed = len(existing)
    strategy_label, strategy_rationale = infer_strategy(n_completed, budget_remaining=budget_remaining)
    constraint_warnings = _constraint_warnings_for_runs(req, plan)
    if acquisition_scores is None and any(r.ai_suggested for r in plan.runs):
        acquisition_scores = legacy_acquisition_scores(
            plan,
            existing,
            n_suggest=len([r for r in plan.runs if r.ai_suggested]),
        )

    anomalies = detect_anomalies(req, existing)
    run_explanations = build_run_explanations(
        req,
        plan,
        existing,
        strategy_label=strategy_label,
        acquisition_scores=acquisition_scores,
        constraint_warnings_by_run=constraint_warnings,
    )
    next_action = recommend_next_action(
        n_completed=n_completed,
        strategy_label=strategy_label,
        budget_remaining=budget_remaining,
        anomalies=anomalies,
    )
    return {
        "strategy_label": strategy_label,
        "strategy_rationale": strategy_rationale,
        "run_explanations": run_explanations,
        "anomalies": anomalies,
        "recommended_next_action": next_action,
        "budget_remaining": budget_remaining,
    }


def enrich_active_doe_result(
    result: ActiveDoeResult,
    req: Requirement,
    existing: list[ExperimentRecord],
    *,
    budget_remaining: int | None = None,
) -> ActiveDoeResult:
    plan = resample_plan_for_constraints(req, result.plan)
    base = result.model_copy(update={"plan": plan}) if plan is not result.plan else result
    meta = build_adaptive_metadata(req, base.plan, existing, budget_remaining=budget_remaining)
    return base.model_copy(update=meta)


def enrich_baybe_result(
    result: BaybeRecommendResult,
    req: Requirement,
    existing: list[ExperimentRecord],
    *,
    budget_remaining: int | None = None,
) -> BaybeRecommendResult:
    plan = resample_plan_for_constraints(req, result.plan)
    base = result.model_copy(update={"plan": plan}) if plan is not result.plan else result
    meta = build_adaptive_metadata(req, base.plan, existing, budget_remaining=budget_remaining)
    return base.model_copy(update=meta)

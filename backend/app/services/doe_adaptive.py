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


def _constraint_warnings_for_runs(
    req: Requirement,
    plan: DOEPlan,
) -> dict[int, list[str]]:
    """Post-recommend formulation validation (constraint propagation lite)."""
    from ..domain.formulation_gate import validate_formulations
    from ..pipeline import reconstruct

    warnings_by_run: dict[int, list[str]] = {}
    suggested = [r for r in plan.runs if r.ai_suggested] or plan.runs
    for run in suggested:
        try:
            form = reconstruct.formulation_from_factors(req, run.natural)
            form.name = f"DOE run {run.run_id}"
            _, warnings = validate_formulations([form], req=req)
            if warnings:
                warnings_by_run[run.run_id] = warnings[:3]
        except Exception:
            continue
    return warnings_by_run


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
    meta = build_adaptive_metadata(req, result.plan, existing, budget_remaining=budget_remaining)
    return result.model_copy(update=meta)


def enrich_baybe_result(
    result: BaybeRecommendResult,
    req: Requirement,
    existing: list[ExperimentRecord],
    *,
    budget_remaining: int | None = None,
) -> BaybeRecommendResult:
    meta = build_adaptive_metadata(req, result.plan, existing, budget_remaining=budget_remaining)
    return result.model_copy(update=meta)

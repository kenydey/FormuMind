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


def resample_plan_for_constraints(
    req: Requirement,
    plan: DOEPlan,
    *,
    max_rounds: int = 2,
    resample_fn=None,
) -> DOEPlan:
    """Swap AI-suggested runs that fail gate validation with cleaner alternates.

    v16 P2-9: BayBE 计划所有 run 的 ai_suggested=True，替换池恒空。
    调用方可传 resample_fn(n) 生成 n 个候补 run（BayBE 路径用 searchspace 补采样）。
    """
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
        # v16 P2-9: 替换池空时用 resample_fn 补采样，保证 batch 恒满。
        if not alternates and resample_fn is not None:
            try:
                alternates = resample_fn(len(bad_suggested)) or []
            except Exception:  # noqa: BLE001 - fail-open
                alternates = []
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
                # v27 P2-19: 两侧 infeasible 取 OR —— KG/物理门是对整批共享
                # skeleton 的判定，标记写在原始 run 上；LHS 候补 run 从未经过
                # 这两个门。v10 只保留了替换源一侧，原始 run 的标记被静默丢弃。
                # v28: 去重（两侧可能携带相同 reason）。
                _reasons = list(dict.fromkeys(
                    r
                    for r in (run.infeasible_reason, replacement.infeasible_reason)
                    if r
                ))
                new_runs.append(
                    DOERun(
                        run_id=run.run_id,
                        coded=replacement.coded,
                        natural=replacement.natural,
                        ai_suggested=True,
                        infeasible=run.infeasible or replacement.infeasible,
                        infeasible_reason="；".join(_reasons),
                    )
                )
            else:
                new_runs.append(run)
        note = current.notes or ""
        if bad_suggested:
            # v29 M-15: resample note 去重计数 —— 此前多次调用会重复追加，
            # 且计数可能不对。现先 strip 旧的 resample note 再追加。
            import re

            note = re.sub(r"\s*\|\s*约束重采样：替换 \d+ 个不合格 AI 点", "", note).strip(" |")
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
    resample_fn=None,
) -> BaybeRecommendResult:
    plan = resample_plan_for_constraints(req, result.plan, resample_fn=resample_fn)
    base = result.model_copy(update={"plan": plan}) if plan is not result.plan else result
    meta = build_adaptive_metadata(req, base.plan, existing, budget_remaining=budget_remaining)
    return base.model_copy(update=meta)

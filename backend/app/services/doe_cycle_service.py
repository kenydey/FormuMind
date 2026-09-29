"""
DOE cycle service for closed-loop automation.

Executes one iteration of the Bayesian optimization closed-loop:

    prior measurements (training registry)
        -> candidate formulations (Top-12, recommendation service)
        -> next experiment points (BayBE, seeded with prior measurements;
           LHS fallback)
        -> persist experiments as pending + provenance links

The closed-loop property: round N+1 reads round N's measured results from
the training registry (populated by workbench sync / CSV import), so the
optimizer actually learns across cycles instead of re-seeding from scratch.
"""
from __future__ import annotations

import logging
from typing import Any

from ..domain.schemas import ExperimentRecord, Requirement
from ..db.database import default_session_factory
from ..db.session_utils import commit_session
from ..db.models import DOECycleRunRow, ExperimentRow

logger = logging.getLogger(__name__)


def record_cycle_run(
    *,
    project_id: str,
    domain: str,
    engine: str,
    prior_measurement_count: int,
    experiment_count: int,
    status: str,
    best_objective_value: float | None = None,
    target_value: float | None = None,
    objective_metric: str | None = None,
    objective_direction: str | None = None,
    convergence_reason: str | None = None,
) -> None:
    """Wave 3-2: persist one closed-loop cycle execution for observability.

    P2-4 adds objective tracking columns (all nullable; old callers unaffected).
    Fail-open: a recording failure must never break the cycle itself.
    """
    try:
        factory = default_session_factory()
        with commit_session(factory) as session:
            session.add(
                DOECycleRunRow(
                    project_id=project_id or "",
                    domain=domain or "",
                    engine=engine or "",
                    prior_measurement_count=prior_measurement_count,
                    experiment_count=experiment_count,
                    status=status,
                    best_objective_value=best_objective_value,
                    target_value=target_value,
                    objective_metric=objective_metric or "",
                    objective_direction=objective_direction or "",
                    convergence_reason=convergence_reason or "",
                )
            )
    except Exception as e:  # noqa: BLE001 - observability must not break cycles
        logger.warning("Failed to record DOE cycle run: %s", e)


def load_prior_measurements(requirement: Requirement) -> list[ExperimentRecord]:
    """Read measured experiments from the training registry.

    This is the closed-loop feedback channel: workbench sync and CSV import
    push measured rows into the registry; the next cycle consumes them here.
    Fail-open: registry errors degrade to an empty list (cold start).
    """
    try:
        from .training import registry

        records = registry.records_for(
            requirement.domain, project_id=requirement.project_id or ""
        )
        logger.info("Loaded %d prior measurements for DOE cycle", len(records))
        return list(records)
    except Exception as e:  # pragma: no cover - defense in depth
        logger.warning("Failed to load prior measurements, cold start: %s", e)
        return []


def build_candidate_formulations(requirement: Requirement) -> list:
    """Step 1 (plan building): Top-12 candidate formulations.

    Fail-open: falls back to a single baseline formulation, with error-level
    logging so silent degradation is visible.
    """
    # 注意：RecommendFormulationsRequest 的 n 约束为 le=12；n=20 会恒抛
    # ValidationError 并被吞掉导致恒回退 baseline（P1 B-9）。
    try:
        from ..api.formulations import (
            RecommendFormulationsRequest,
            recommend_formulations as recommendation_recommend_formulations,
        )

        req_obj = RecommendFormulationsRequest(requirement=requirement, n=12)
        rec_result = recommendation_recommend_formulations(req_obj)
        candidates = rec_result.formulations
        logger.info("Got %d candidate formulations", len(candidates))
        return candidates
    except Exception as e:
        # Fail-open：回退单条 baseline，但必须打 error 级日志，避免静默降级
        # （Top-12 候选从未生效的故障模式）。
        logger.error("Failed to get recommendations: %s", e)
        from ..domain import knowledge

        baseline = knowledge.baseline_formulation(requirement)
        candidates = [baseline] if baseline else []
        logger.warning("Using fallback: %d formulations", len(candidates))
        return candidates


def generate_experiment_dicts(
    requirement: Requirement,
    prior_measurements: list[ExperimentRecord],
    budget_remaining: int | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Step 2 (execution dispatch): generate the next experiment points.

    BayBE is seeded with ``prior_measurements`` so round N+1 learns from
    round N. When BayBE is unavailable, falls back to LHS (which itself
    falls back to registry records inside ``active_learning_doe``).

    Returns ``(engine, dicts)`` where engine is ``"baybe"`` or ``"lhs"``
    (Wave 3-2: observability).
    """
    try:
        from ..services.engines.baybe_engine import BaybeCampaignEngine

        baybe_engine = BaybeCampaignEngine()
        if not baybe_engine.available():
            logger.warning("Baybe engine not available, falling back to LHS")
            return _generate_via_lhs(
                requirement, prior_measurements, budget_remaining=budget_remaining
            )
        return _generate_via_baybe(
            requirement, prior_measurements, baybe_engine, budget_remaining=budget_remaining
        )
    except Exception as e:
        logger.error("Failed to generate experiments: %s", e)
        raise


def _generate_via_lhs(
    requirement: Requirement,
    prior_measurements: list[ExperimentRecord],
    budget_remaining: int | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    from ..services.active_learning import active_learning_doe

    # A-7: budget-aware fallback — never generate points when the budget is
    # exhausted, even if called outside run_doe_cycle's P2-4 hard stop.
    if budget_remaining is not None and budget_remaining <= 0:
        logger.info(
            "LHS fallback held: budget exhausted (remaining=%s)", budget_remaining
        )
        return "lhs", []
    active_result = active_learning_doe(
        req=requirement,
        existing=prior_measurements or None,  # None -> registry fallback inside
        n_suggest=5,
        design="lhs",
        engine="auto",
        workbench_campaign_id=None,
        budget_remaining=budget_remaining,
    )
    experiment_dicts = [_run_to_dict(run) for run in active_result.plan.runs]
    logger.info("Generated %d experiments via LHS fallback", len(experiment_dicts))
    return "lhs", experiment_dicts


def _generate_via_baybe(
    requirement: Requirement,
    prior_measurements: list[ExperimentRecord],
    baybe_engine: Any,
    budget_remaining: int | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    active_result = baybe_engine.recommend(
        req=requirement,
        measurements=prior_measurements,  # closed-loop: seed with real data
        batch_size=5,
        design="baybe_active",
        workbench_campaign_id=None,
        budget_remaining=budget_remaining,
    )
    experiment_dicts = [_run_to_dict(run) for run in active_result.plan.runs]
    logger.info("Generated %d experiments via Baybe", len(experiment_dicts))
    return "baybe", experiment_dicts


def _run_to_dict(run: Any) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "coded_factors": run.coded,
        "natural_factors": run.natural,
        "ai_suggested": run.ai_suggested,
        "infeasible": run.infeasible,
        "infeasible_reason": run.infeasible_reason,
    }


def persist_experiments(
    requirement: Requirement,
    experiment_dicts: list[dict[str, Any]],
    candidate_formulations: list,
) -> dict[str, Any]:
    """Step 3 (result convergence): persist pending experiments + provenance."""
    factory = default_session_factory()
    with commit_session(factory) as session:
        experiment_ids = []
        for exp_dict in experiment_dicts:
            # 只持久化数值型 factors。注意：不要把嵌套 dict（如旧代码的
            # "_doe_metadata"）塞进 ExperimentRow.factors —— 下游消费者
            # 按 dict[str, float] 使用（similarity 里的 `cv > 0` 会直接
            # TypeError；SimilarFormulationMatch 的 factors 字段也会校验
            # 失败），且 db/store 的 _coerce_factor_floats 本就丢弃这类
            # metadata blob，没有任何消费者会读回它。
            base_factors = exp_dict["natural_factors"].copy()
            doe_metadata = {
                "ai_suggested": exp_dict["ai_suggested"],
                "infeasible": exp_dict["infeasible"],
                "infeasible_reason": exp_dict["infeasible_reason"] or "",
            }

            experiment = ExperimentRow(
                item_id=None,
                domain=requirement.domain.value,
                project_id=(requirement.project_id or ""),
                factors=base_factors,
                cure_temperature_c=None,
                measured={},
                source="lab",
                label=str(exp_dict["run_id"]),
            )
            session.add(experiment)
            session.flush()  # Get the ID
            logger.debug(
                "Saved experiment %s doe_metadata=%s",
                experiment.id,
                doe_metadata,
            )
            experiment_ids.append(str(experiment.id))

        logger.info("Saved %d experiments to database", len(experiment_ids))

        # W2-6 (P1-2): link each saved experiment (run) to the formulation
        # candidates it was generated from. Fail-open: never break the cycle.
        try:
            from . import provenance as _prov

            _fids = [
                _prov.formulation_id_for(f)
                for f in (candidate_formulations or [])[:5]
            ]
            for _eid in experiment_ids:
                for _fid in _fids:
                    _prov.link("run", _eid, "formulation", _fid, "tests")
        except Exception:
            pass

        return {
            "experiment_ids": experiment_ids,
            "status": "success",
            "count": len(experiment_ids),
            "message": f"Generated {len(experiment_ids)} new experiments",
        }


def _hold_stub(
    *,
    reason: str,
    message: str,
    best_objective_value: float | None,
    target_value: float | None,
    objective_metric: str,
    objective_direction: str,
) -> dict[str, Any]:
    """P2-4: converged-hold stub — no new experiments are generated."""
    return {
        "experiment_ids": [],
        "status": "success",
        "count": 0,
        "message": message,
        "engine": "converged-hold",
        "held": True,
        "convergence_reason": reason,
        "best_objective_value": best_objective_value,
        "target_value": target_value,
        "objective_metric": objective_metric,
        "objective_direction": objective_direction,
    }


def run_doe_cycle(
    requirement: Requirement, budget_remaining: int | None = None
) -> dict[str, Any]:
    """Execute one DOE cycle for closed-loop automation.

    Orchestrates: load prior measurements -> build candidates ->
    generate points -> persist.

    P2-4: hard stops before any generation —
    ``budget_remaining <= 0`` or the primary objective target already met
    returns a converged-hold stub (no experiments generated).
    """
    logger.info("Starting DOE cycle for requirement: %s", requirement.domain)

    # 0. Closed-loop feedback: round N's measured results seed round N+1.
    prior_measurements = load_prior_measurements(requirement)
    n_prior = len(prior_measurements)
    project_id = requirement.project_id or ""
    domain = requirement.domain.value if requirement.domain else ""

    # P2-4: objective tracking for the observability row.
    from .auto_loop import (
        best_objective_value,
        primary_objective_spec,
        target_achieved,
    )

    obj_spec = primary_objective_spec(requirement)
    best = best_objective_value(
        prior_measurements, obj_spec.metric, obj_spec.direction
    )
    obj_kwargs = dict(
        best_objective_value=best,
        target_value=obj_spec.target_value,
        objective_metric=obj_spec.metric,
        objective_direction=obj_spec.direction,
    )

    def _record(engine: str, n_experiments: int, status: str, **extra: Any) -> None:
        # Wave 3-2: every terminal path leaves an observability row.
        record_cycle_run(
            project_id=project_id,
            domain=domain,
            engine=engine,
            prior_measurement_count=n_prior,
            experiment_count=n_experiments,
            status=status,
            **{**obj_kwargs, **extra},
        )

    # P2-4 hard stop 1: budget exhausted — before any candidate/generation work.
    if budget_remaining is not None and budget_remaining <= 0:
        logger.info("DOE cycle held: budget exhausted (remaining=%s)", budget_remaining)
        _record("converged-hold", 0, "success", convergence_reason="budget_exhausted")
        return _hold_stub(
            reason="budget_exhausted",
            message=f"预算已耗尽（remaining={budget_remaining}），保留上一轮 DOE，无需新实验建议",
            **obj_kwargs,
        )

    # P2-4 hard stop 2: target already achieved by prior measurements.
    if target_achieved(best, obj_spec):
        logger.info(
            "DOE cycle held: target achieved (%s best=%s target=%s)",
            obj_spec.metric,
            best,
            obj_spec.target_value,
        )
        _record("converged-hold", 0, "success", convergence_reason="target_achieved")
        return _hold_stub(
            reason="target_achieved",
            message=(
                f"目标已达成（{obj_spec.metric} 最优测量值 {best:g}"
                f"已达到目标 {obj_spec.target_value:g}），无需新实验建议"
            ),
            **obj_kwargs,
        )

    # 1. Candidate formulations (Top-12).
    candidate_formulations = build_candidate_formulations(requirement)
    if not candidate_formulations:
        logger.error("No candidate formulations available")
        _record("", 0, "error")
        return {"experiment_ids": [], "status": "error", "message": "No candidates"}

    # 2. Next experiment points (BayBE seeded with priors, else LHS).
    try:
        engine, experiment_dicts = generate_experiment_dicts(
            requirement, prior_measurements, budget_remaining=budget_remaining
        )
    except Exception as e:
        _record("", 0, "error")
        return {"experiment_ids": [], "status": "error", "message": f"Generation failed: {e}"}

    # 3. Persist as pending + provenance links.
    try:
        result = persist_experiments(
            requirement, experiment_dicts, candidate_formulations
        )
    except Exception as e:
        logger.error("Failed to save experiments: %s", e)
        _record(engine, 0, "error")
        return {"experiment_ids": [], "status": "error", "message": f"Save failed: {e}"}
    _record(engine, result.get("count", 0), "success")
    # Wave 3-2: enrich the task result with observability fields.
    result["engine"] = engine
    result["prior_measurement_count"] = n_prior
    return result

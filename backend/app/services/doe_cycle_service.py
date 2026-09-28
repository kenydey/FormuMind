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
from ..db.models import ExperimentRow

logger = logging.getLogger(__name__)


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
) -> list[dict[str, Any]]:
    """Step 2 (execution dispatch): generate the next experiment points.

    BayBE is seeded with ``prior_measurements`` so round N+1 learns from
    round N. When BayBE is unavailable, falls back to LHS (which itself
    falls back to registry records inside ``active_learning_doe``).
    """
    try:
        from ..services.engines.baybe_engine import BaybeCampaignEngine

        baybe_engine = BaybeCampaignEngine()
        if not baybe_engine.available():
            logger.warning("Baybe engine not available, falling back to LHS")
            return _generate_via_lhs(requirement, prior_measurements)
        return _generate_via_baybe(requirement, prior_measurements, baybe_engine)
    except Exception as e:
        logger.error("Failed to generate experiments: %s", e)
        raise


def _generate_via_lhs(
    requirement: Requirement,
    prior_measurements: list[ExperimentRecord],
) -> list[dict[str, Any]]:
    from ..services.active_learning import active_learning_doe

    active_result = active_learning_doe(
        req=requirement,
        existing=prior_measurements or None,  # None -> registry fallback inside
        n_suggest=5,
        design="lhs",
        engine="auto",
        workbench_campaign_id=None,
        budget_remaining=None,
    )
    experiment_dicts = [_run_to_dict(run) for run in active_result.plan.runs]
    logger.info("Generated %d experiments via LHS fallback", len(experiment_dicts))
    return experiment_dicts


def _generate_via_baybe(
    requirement: Requirement,
    prior_measurements: list[ExperimentRecord],
    baybe_engine: Any,
) -> list[dict[str, Any]]:
    active_result = baybe_engine.recommend(
        req=requirement,
        measurements=prior_measurements,  # closed-loop: seed with real data
        batch_size=5,
        design="baybe_active",
        workbench_campaign_id=None,
    )
    experiment_dicts = [_run_to_dict(run) for run in active_result.plan.runs]
    logger.info("Generated %d experiments via Baybe", len(experiment_dicts))
    return experiment_dicts


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


def run_doe_cycle(requirement: Requirement) -> dict[str, Any]:
    """Execute one DOE cycle for closed-loop automation.

    Orchestrates: load prior measurements -> build candidates ->
    generate points -> persist. Same signature as before.
    """
    logger.info("Starting DOE cycle for requirement: %s", requirement.domain)

    # 0. Closed-loop feedback: round N's measured results seed round N+1.
    prior_measurements = load_prior_measurements(requirement)

    # 1. Candidate formulations (Top-12).
    candidate_formulations = build_candidate_formulations(requirement)
    if not candidate_formulations:
        logger.error("No candidate formulations available")
        return {"experiment_ids": [], "status": "error", "message": "No candidates"}

    # 2. Next experiment points (BayBE seeded with priors, else LHS).
    try:
        experiment_dicts = generate_experiment_dicts(requirement, prior_measurements)
    except Exception as e:
        return {"experiment_ids": [], "status": "error", "message": f"Generation failed: {e}"}

    # 3. Persist as pending + provenance links.
    try:
        return persist_experiments(
            requirement, experiment_dicts, candidate_formulations
        )
    except Exception as e:
        logger.error("Failed to save experiments: %s", e)
        return {"experiment_ids": [], "status": "error", "message": f"Save failed: {e}"}

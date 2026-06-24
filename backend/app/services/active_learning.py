"""Active Learning DOE advisor (v0.5).

Uses the currently trained surrogate models (or the empirical predictor when
no lab data is available) to score un-run DOE grid points by Expected
Improvement, then returns the N most informative experiments. Replaces the
random sampling in standard DOE with surrogate-guided selection.

v0.7 adds optional baybe Campaign engine for continuous constrained recommendations.
"""
from __future__ import annotations

import uuid

from ..domain.schemas import ActiveDoeResult, DOEPlan, DOERun, ExperimentRecord, ProductDomain, Requirement


_MIN_TRAIN_SAMPLES = 3  # minimum records needed before surrogate-guided selection


def _surrogate_score(
    natural: dict[str, float],
    domain: ProductDomain,
    existing: list[ExperimentRecord],
    objective_metric: str,
) -> tuple[float, float]:
    """Return (mean, std) estimate for a candidate point.

    Uses trained registry when ≥ _MIN_TRAIN_SAMPLES records exist; otherwise
    falls back to the empirical predictor + a fixed nominal uncertainty.
    """
    from ..domain import features, knowledge
    from ..domain.schemas import Formulation
    from ..pipeline import reconstruct
    from ..services import predictor
    from ..services.training import registry

    try:
        form: Formulation = reconstruct.formulation_from_factors(domain, natural)
    except Exception:
        form = knowledge.baseline_formulation(Requirement(domain=domain))

    props = predictor.predict(form)
    mean = props.get(objective_metric, 0.0)

    if len(existing) >= _MIN_TRAIN_SAMPLES:
        vec = features.vector(form, None)
        out = registry.predict_with_std(domain, objective_metric, vec)
        if out is not None:
            mean, std, _ = out
            return float(mean), float(std)

    # Empirical uncertainty: 20% relative
    std = abs(mean) * 0.20 + 1e-3
    return float(mean), float(std)


def _ei_acquisition(mean: float, std: float, y_best: float, kappa: float = 1.0) -> float:
    """Expected Improvement acquisition (Gaussian, analytic).

    Falls back to UCB = mean + kappa·std when scipy is unavailable.
    """
    if std < 1e-9:
        return 0.0
    try:
        from scipy import stats  # type: ignore

        z = (mean - y_best) / std
        return float((mean - y_best) * stats.norm.cdf(z) + std * stats.norm.pdf(z))
    except Exception:
        return float(mean + kappa * std)


def suggest_next_experiments(
    plan: DOEPlan,
    existing: list[ExperimentRecord],
    n_suggest: int = 4,
    objective_metric: str | None = None,
) -> list[DOERun]:
    """Return the n_suggest most informative un-run DOE experiments.

    Scores all runs in the plan by EI, marks the top ones with
    ``ai_suggested=True``, and returns them in priority order.

    If training data is insufficient (< _MIN_TRAIN_SAMPLES), falls back
    to selecting the N runs with highest empirical-predictor variance
    (most spread in coded space from existing observations).
    """
    from ..pipeline.workflow import OBJECTIVE

    if plan.domain is None:
        return plan.runs[:n_suggest]

    obj_metric = objective_metric or OBJECTIVE.get(plan.domain, "salt_spray_hours")

    y_best = 0.0
    if existing:
        y_best = max(rec.measured.get(obj_metric, 0.0) for rec in existing)

    scored: list[tuple[float, DOERun]] = []
    for run in plan.runs:
        mean, std = _surrogate_score(run.natural, plan.domain, existing, obj_metric)
        acq = _ei_acquisition(mean, std, y_best)
        scored.append((acq, run))

    scored.sort(key=lambda t: t[0], reverse=True)
    result = []
    for _, run in scored[:n_suggest]:
        updated = DOERun(
            run_id=run.run_id,
            coded=run.coded,
            natural=run.natural,
            ai_suggested=True,
        )
        result.append(updated)
    return result


def _legacy_active_learning_doe(
    req: Requirement,
    existing: list[ExperimentRecord] | None,
    n_suggest: int,
    design: str,
    *,
    doe_engine: str = "auto",
) -> DOEPlan:
    from ..pipeline.workflow import build_doe

    plan = build_doe(req, design=design, engine=doe_engine)
    plan.notes = f"engine=legacy; AI 主动选点 (n={n_suggest}, design={design})"

    suggested_ids = {
        r.run_id
        for r in suggest_next_experiments(plan, existing or [], n_suggest=n_suggest)
    }
    plan.runs = [
        DOERun(
            run_id=r.run_id,
            coded=r.coded,
            natural=r.natural,
            ai_suggested=(r.run_id in suggested_ids),
        )
        for r in plan.runs
    ]
    return plan


def active_learning_doe(
    req: Requirement,
    existing: list[ExperimentRecord] | None = None,
    n_suggest: int = 4,
    design: str = "lhs",
    *,
    engine: str = "auto",
    campaign_state: str | None = None,
    doe_engine: str = "auto",
) -> ActiveDoeResult:
    """Generate a DOE plan and annotate the most informative runs."""
    eng = (engine or "auto").lower()
    existing = existing or []
    if not existing:
        from ..services.training import registry

        existing = list(registry.records_for(req.domain))

    if eng in ("baybe", "auto"):
        from ..services.engines.baybe_engine import BaybeCampaignEngine
        from ..services.engines.doe_registry import baybe_available

        if eng == "baybe" or baybe_available():
            try:
                baybe = BaybeCampaignEngine()
                if baybe.available():
                    result = baybe.recommend(
                        req,
                        campaign_state=campaign_state,
                        measurements=existing,
                        batch_size=n_suggest,
                        design=f"baybe_{design}",
                    )
                    result.plan.plan_id = uuid.uuid4().hex
                    result.plan.domain = req.domain
                    from ..pipeline.workflow import _cache_plan

                    _cache_plan(result.plan)
                    return ActiveDoeResult(
                        plan=result.plan,
                        campaign_state=result.campaign_state,
                        engine="baybe",
                    )
            except Exception:
                if eng == "baybe":
                    raise

    plan = _legacy_active_learning_doe(req, existing, n_suggest, design, doe_engine=doe_engine)
    from ..pipeline.workflow import _cache_plan

    _cache_plan(plan)
    return ActiveDoeResult(plan=plan, campaign_state=None, engine="legacy")

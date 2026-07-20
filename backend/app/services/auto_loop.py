"""Self-driving research loop orchestration (v0.6, P0).

Wires together the pieces that already exist independently into a single
one-click iteration:

    existing lab data  →  (auto-fetch from registry)
                       →  optimize with the freshly-blended models
                       →  generate the next active-learning DOE batch
                       →  report model RMSE/R² status

Pure CPU / pure Python: reuses workflow.run_optimization (which blends trained
models via predictor._blend_trained), active_learning.active_learning_doe, and
the training registry. Degrades gracefully when there is no lab data yet
(empirical prior + LHS point selection).
"""
from __future__ import annotations

from collections.abc import Callable

from ..config import get_settings
from ..domain.schemas import DOEPlan, LoopReport, OptimizationResult, ProductDomain, Requirement


def _rmse_by_metric(domain: ProductDomain) -> tuple[list, dict[str, float]]:
    """Return (model_info_for_domain, {metric: rmse}) from the trained registry."""
    from .training import registry

    infos = [m for m in registry.info() if m.domain == domain]
    rmse = {m.metric: m.rmse for m in infos}
    return infos, rmse


def rmse_plateau_detected(
    history: list[dict[str, float]],
    *,
    eps: float,
    patience: int,
) -> bool:
    """True when the last ``patience`` consecutive RMSE steps are flat for all metrics."""
    if patience < 1 or len(history) < patience + 1:
        return False
    metrics: set[str] = set()
    for snap in history:
        metrics.update(snap.keys())
    if not metrics:
        return False
    recent = history[-(patience + 1) :]
    for i in range(1, len(recent)):
        prev, curr = recent[i - 1], recent[i]
        for metric in metrics:
            if metric not in prev or metric not in curr:
                return False
            if abs(curr[metric] - prev[metric]) >= eps:
                return False
    return True


def _stub_optimization(req: Requirement) -> OptimizationResult:
    from ..domain.project_spec import primary_objective

    return OptimizationResult(
        iterations=0,
        objective=primary_objective(req),
        history=[],
        top_formulations=[],
        engine="skipped-converged",
    )


def _stub_doe(req: Requirement) -> DOEPlan:
    from ..domain.schemas import DOEFactor, DOERun

    levers = req.levers or []
    factors = [
        DOEFactor(name=lev.name, low=lev.low, high=lev.high, unit=lev.unit)
        for lev in levers[:6]
    ]
    natural = {lev.name: round((lev.low + lev.high) / 2, 3) for lev in levers[:6]}
    return DOEPlan(
        design="converged-hold",
        factors=factors,
        runs=[DOERun(run_id=1, coded={}, natural=natural)] if natural else [],
        notes="模型 RMSE 已收敛 — 保留上一轮 DOE，无需新实验建议",
        plan_id="converged",
        domain=req.domain,
    )


def loop_iterate(
    req: Requirement,
    optimize_iterations: int = 24,
    n_suggest: int = 4,
    progress_cb: Callable[[float, str], None] | None = None,
    *,
    optimize_engine: str = "auto",
    doe_engine: str = "auto",
    workbench_campaign_id: int | None = None,
    campaign_state: str | None = None,
    prior_rmse_history: list[dict[str, float]] | None = None,
    prior_optimization: OptimizationResult | None = None,
    prior_next_doe: DOEPlan | None = None,
) -> LoopReport:
    """Run one full turn of the self-driving loop and bundle the result."""
    from . import active_learning
    from ..pipeline import workflow
    from .training import registry

    settings = get_settings()

    if progress_cb:
        progress_cb(0.05, "loading lab history")
    records = registry.records_for(req.domain)
    model_info, rmse = _rmse_by_metric(req.domain)

    history = list(prior_rmse_history or [])
    full_history = history + [rmse] if rmse else history
    converged = (
        settings.loop_convergence_enabled
        and rmse
        and rmse_plateau_detected(
            full_history,
            eps=settings.loop_convergence_eps,
            patience=settings.loop_convergence_patience,
        )
    )

    if converged:
        if progress_cb:
            progress_cb(1.0, "converged — skipping optimize")
        optimization = prior_optimization or _stub_optimization(req)
        next_doe = prior_next_doe or _stub_doe(req)
        return LoopReport(
            domain=req.domain.value,
            total_records=len(records),
            model_info=model_info,
            rmse_by_metric=rmse,
            optimization=optimization,
            next_doe=next_doe,
            engine=optimization.engine,
            campaign_state=campaign_state,
            converged=True,
            loop_message="模型 RMSE 已进入平台期，建议停止闭环迭代",
        )

    if progress_cb:
        progress_cb(0.1, "optimizing with latest models")

    def _opt_progress(p: float, msg: str) -> None:
        if progress_cb:
            progress_cb(0.1 + p * 0.75, msg)

    optimization = workflow.run_optimization(
        req,
        iterations=optimize_iterations,
        progress_cb=_opt_progress,
        engine=optimize_engine,
        existing_records=records,
        campaign_state=campaign_state,
        workbench_campaign_id=workbench_campaign_id,
    )

    if progress_cb:
        progress_cb(0.9, "selecting next experiments")
    next_result = active_learning.active_learning_doe(
        req,
        existing=records,
        n_suggest=n_suggest,
        design="lhs",
        engine=doe_engine,
        doe_engine=doe_engine,
        campaign_state=campaign_state,
        workbench_campaign_id=workbench_campaign_id,
    )

    if progress_cb:
        progress_cb(1.0, "done")

    return LoopReport(
        domain=req.domain.value,
        total_records=len(records),
        model_info=model_info,
        rmse_by_metric=rmse,
        optimization=optimization,
        next_doe=next_result.plan,
        engine=optimization.engine,
        campaign_state=getattr(next_result, "campaign_state", None) or campaign_state,
        converged=False,
        loop_message="",
    )

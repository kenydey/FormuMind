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

from ..domain.schemas import LoopReport, ProductDomain, Requirement


def _rmse_by_metric(domain: ProductDomain) -> tuple[list, dict[str, float]]:
    """Return (model_info_for_domain, {metric: rmse}) from the trained registry."""
    from .training import registry

    infos = [m for m in registry.info() if m.domain == domain]
    rmse = {m.metric: m.rmse for m in infos}
    return infos, rmse


def loop_iterate(
    req: Requirement,
    optimize_iterations: int = 24,
    n_suggest: int = 4,
    progress_cb: Callable[[float, str], None] | None = None,
) -> LoopReport:
    """Run one full turn of the self-driving loop and bundle the result."""
    from . import active_learning
    from ..pipeline import workflow
    from .training import registry

    if progress_cb:
        progress_cb(0.05, "loading lab history")
    records = registry.records_for(req.domain)
    model_info, rmse = _rmse_by_metric(req.domain)

    if progress_cb:
        progress_cb(0.1, "optimizing with latest models")

    def _opt_progress(p: float, msg: str) -> None:
        # Map the optimizer's 0..1 into the 0.1..0.85 band of the loop.
        if progress_cb:
            progress_cb(0.1 + p * 0.75, msg)

    optimization = workflow.run_optimization(
        req, iterations=optimize_iterations, progress_cb=_opt_progress
    )

    if progress_cb:
        progress_cb(0.9, "selecting next experiments")
    next_doe = active_learning.active_learning_doe(
        req, existing=records, n_suggest=n_suggest, design="lhs"
    )

    if progress_cb:
        progress_cb(1.0, "done")

    return LoopReport(
        domain=req.domain.value,
        total_records=len(records),
        model_info=model_info,
        rmse_by_metric=rmse,
        optimization=optimization,
        next_doe=next_doe,
        engine=optimization.engine,
    )

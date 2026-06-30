"""Baybe Campaign engine — stateless via JSON serialization."""
from __future__ import annotations

import logging

from ...domain.objective_contract import align_dataframe_measurement_columns, objective_metrics
from ...domain.schemas import (
    BaybeRecommendResult,
    ExperimentRecord,
    ObjectiveSpec,
    OptimizationResult,
    Requirement,
)
from ...pipeline import reconstruct
from ...pipeline.workflow import (
    OBJECTIVE,
    _score_and_validate,
    default_objectives,
    process_for,
)
from ...services import predictor
from ...config import get_settings
from .adapters.baybe_objective_builder import build_objective_from_specs, primary_metric
from .adapters.baybe_space_builder import build_searchspace, factors_for_requirement, factors_from_campaign
from .adapters.doe_adapter import dataframe_to_doe_plan
from .adapters.measurements_adapter import records_to_dataframe, surrogate_measurements_from_plan
from .campaign_objectives import resolve_campaign_objectives
from .doe_registry import baybe_available, build_doe_plan

log = logging.getLogger(__name__)


def fetch_campaign_data_for_baybe(
    campaign_id: int,
    req: Requirement | None = None,
    *,
    store=None,
):
    """Load completed workbench rows for BayBE ``add_measurements``.

    Returns ``(actual_X, measurements_Y)`` where measurement columns follow
    ``Campaign.objectives_snapshot`` order (SSOT = ``ObjectiveSpec.metric``).
    Data is read from the campaign store (Datalab SSOT or sqlite fallback).
    """
    import pandas as pd

    from ...db.campaign_store import get_campaign_store
    from ...domain.schemas import ProductDomain

    if req is None:
        req = Requirement(domain=ProductDomain.anticorrosion_coating)

    campaign_store = store or get_campaign_store()
    objectives = resolve_campaign_objectives(campaign_store, campaign_id, req)
    metrics = objective_metrics(objectives)

    rows = campaign_store.get_experiments_sync(campaign_id)
    if not rows:
        return pd.DataFrame(), pd.DataFrame()

    actual_X = pd.DataFrame([dict(r.actual_params or r.planned_params or {}) for r in rows])

    if not metrics:
        first_meas = rows[0].measurements or {}
        metrics = list(first_meas.keys())

    meas_rows: list[dict] = []
    for r in rows:
        raw = dict(r.measurements or {})
        row: dict = {}
        for m in metrics:
            if m not in raw or raw[m] is None or raw[m] == "":
                log.warning("Campaign %s row %s missing measurement %r", campaign_id, r.id, m)
                row[m] = float("nan")
            else:
                try:
                    row[m] = float(raw[m])
                except (TypeError, ValueError):
                    row[m] = float("nan")
        meas_rows.append(row)
    measurements_Y = pd.DataFrame(meas_rows, columns=metrics) if metrics else pd.DataFrame()
    return actual_X, measurements_Y


def workbench_dataframes_to_baybe(actual_X, measurements_Y, metrics: list[str] | None = None):
    """Merge workbench parameter and measurement frames for ``add_measurements``."""
    import pandas as pd

    if actual_X is None or getattr(actual_X, "empty", True):
        return pd.DataFrame()
    if measurements_Y is None or getattr(measurements_Y, "empty", True):
        return actual_X.copy()
    merged = pd.concat([actual_X.reset_index(drop=True), measurements_Y.reset_index(drop=True)], axis=1)
    if metrics:
        merged = align_dataframe_measurement_columns(merged, metrics, log=log)
    return merged


def _prepare_measurement_dataframe(df, metrics: list[str]):
    if df is None or getattr(df, "empty", True):
        return df
    from ...domain.objective_contract import assert_dataframe_measurement_columns

    aligned = align_dataframe_measurement_columns(df, metrics, log=log)
    assert_dataframe_measurement_columns(aligned, metrics)
    return aligned


class BaybeCampaignEngine:
    """Recommend next experiments using baybe Campaign + JSON state roundtrip."""

    def available(self) -> bool:
        return baybe_available()

    def _new_campaign(self, req: Requirement, objectives: list[ObjectiveSpec], factors=None):
        from baybe import Campaign
        from baybe.recommenders import BotorchRecommender, FPSRecommender, TwoPhaseMetaRecommender

        factor_list = factors_for_requirement(req, factors)
        searchspace = build_searchspace(req, factor_list)
        objective = build_objective_from_specs(objectives)
        recommender = TwoPhaseMetaRecommender(
            initial_recommender=FPSRecommender(),
            recommender=BotorchRecommender(),
        )
        return Campaign(searchspace, objective, recommender), factor_list

    def recommend(
        self,
        req: Requirement,
        *,
        campaign_state: str | None = None,
        measurements: list[ExperimentRecord] | None = None,
        batch_size: int = 4,
        design: str = "baybe_active",
        workbench_campaign_id: int | None = None,
        store=None,
    ) -> BaybeRecommendResult:
        if not self.available():
            raise RuntimeError("baybe is not installed (pip install -e '.[baybe,bo,science]')")

        from ...db.campaign_store import get_campaign_store

        campaign_store = store or get_campaign_store()
        from baybe import Campaign

        objectives = resolve_campaign_objectives(campaign_store, workbench_campaign_id, req)
        metrics = objective_metrics(objectives)

        measurements = measurements or []
        wb_factors: list | None = None
        if workbench_campaign_id is not None and campaign_state is None:
            campaign_meta = campaign_store.get_campaign_sync(workbench_campaign_id)
            wb_factors = factors_from_campaign(campaign_meta, req)

        if campaign_state:
            campaign = Campaign.from_json(campaign_state)
            factor_list = wb_factors or factors_for_requirement(req)
        else:
            campaign, factor_list = self._new_campaign(req, objectives, wb_factors)

        df_meas = records_to_dataframe(measurements, req, objectives)
        if not df_meas.empty and metrics:
            df_meas = align_dataframe_measurement_columns(df_meas, metrics, log=log)

        if workbench_campaign_id is not None:
            actual_X, measurements_Y = fetch_campaign_data_for_baybe(
                workbench_campaign_id, req, store=campaign_store
            )
            df_wb = workbench_dataframes_to_baybe(actual_X, measurements_Y, metrics)
            if not df_wb.empty:
                import pandas as pd

                log.info(
                    "Workbench measurements for campaign %s: metrics=%s rows=%d",
                    workbench_campaign_id,
                    metrics,
                    len(df_wb),
                )
                df_wb = _prepare_measurement_dataframe(df_wb, metrics)
                df_meas = (
                    pd.concat([df_meas, df_wb], ignore_index=True)
                    if not df_meas.empty
                    else df_wb
                )

        if not df_meas.empty:
            campaign.add_measurements(_prepare_measurement_dataframe(df_meas, metrics))

        if campaign_state is None and df_meas.empty:
            seed_plan = build_doe_plan(factor_list, "lhs", engine="auto", n=max(batch_size * 2, 8))
            virtual = surrogate_measurements_from_plan(seed_plan, req, None)
            if not virtual.empty and metrics:
                virtual = align_dataframe_measurement_columns(virtual, metrics, log=log)
            if not virtual.empty:
                campaign.add_measurements(virtual.head(min(3, len(virtual))))

        rec_df = campaign.recommend(batch_size=batch_size)
        plan = dataframe_to_doe_plan(rec_df, factor_list, design, engine="baybe", ai_suggested=True)
        return BaybeRecommendResult(
            plan=plan,
            campaign_state=campaign.to_json(),
            engine="baybe",
        )

    def run_optimization(
        self,
        req: Requirement,
        iterations: int = 24,
        *,
        campaign_state: str | None = None,
        measurements: list[ExperimentRecord] | None = None,
        progress_cb=None,
        workbench_campaign_id: int | None = None,
        store=None,
    ) -> OptimizationResult:
        """Iterative baybe batch recommendations scored via FormuMind predictor."""
        from ...db.campaign_store import get_campaign_store

        campaign_store = store or get_campaign_store()
        measurements = list(measurements or [])
        batch_size = max(1, min(4, max(1, iterations // 6)))
        rounds = max(1, iterations // batch_size)
        history: list[float] = []
        best_so_far = float("-inf")
        objectives = resolve_campaign_objectives(campaign_store, workbench_campaign_id, req)
        if not objectives:
            objectives = req.objectives or default_objectives(req.domain)
        process = process_for(req)
        bounds: dict[str, tuple[float, float]] = {}
        ranked: list[tuple[float, object]] = []
        state = campaign_state
        metric = primary_metric(req)
        objective_metric_names = objective_metrics(objectives)
        settings = get_settings()

        for r in range(rounds):
            result = self.recommend(
                req,
                campaign_state=state,
                measurements=measurements,
                batch_size=batch_size,
                design="baybe_opt",
                workbench_campaign_id=workbench_campaign_id,
                store=campaign_store,
            )
            state = result.campaign_state

            for run in result.plan.runs:
                form = _score_and_validate(
                    reconstruct.formulation_from_factors(req, run.natural),
                    process,
                    req,
                )
                score = float(form.score or 0.0)
                for m, val in form.predicted.items():
                    lo, hi = bounds.get(m, (val, val))
                    bounds[m] = (min(lo, val), max(hi, val))
                mo_score = predictor.multi_objective_score(form, objectives, process, bounds)
                combined = max(mo_score, score)
                best_so_far = max(best_so_far, combined)
                history.append(round(best_so_far, 3))
                ranked.append((combined, form))
                measured_vals = {
                    m: form.predicted.get(m, combined if m == metric else form.predicted.get(m, 0.0))
                    for m in objective_metric_names
                }
                measurements.append(
                    ExperimentRecord(
                        domain=req.domain,
                        factors=run.natural,
                        cure_temperature_c=run.natural.get("cure_temperature_c"),
                        measured=measured_vals,
                        source="baybe_opt",
                    )
                )

            if progress_cb:
                progress_cb((r + 1) / rounds, f"baybe batch {r + 1}/{rounds}: best={best_so_far:.3f}")

        ranked.sort(key=lambda t: t[0], reverse=True)
        top = []
        for score, form in ranked[: settings.top_n_formulas]:
            form.name = f"BayBE {req.domain.value} (score {score:.3f})"
            top.append(form)

        return OptimizationResult(
            iterations=iterations,
            objective=OBJECTIVE[req.domain],
            objectives=objectives,
            history=history or [0.0],
            top_formulations=top,
            engine="baybe",
        )

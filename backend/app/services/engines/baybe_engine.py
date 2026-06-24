"""Baybe Campaign engine — stateless via JSON serialization."""
from __future__ import annotations

from ...domain.schemas import (
    BaybeRecommendResult,
    ExperimentRecord,
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
from .adapters.baybe_objective_builder import build_objective, primary_metric
from .adapters.baybe_space_builder import build_searchspace, factors_for_requirement
from .adapters.doe_adapter import dataframe_to_doe_plan
from .adapters.measurements_adapter import records_to_dataframe, surrogate_measurements_from_plan
from .doe_registry import baybe_available, build_doe_plan


class BaybeCampaignEngine:
    """Recommend next experiments using baybe Campaign + JSON state roundtrip."""

    def available(self) -> bool:
        return baybe_available()

    def _new_campaign(self, req: Requirement, factors=None):
        from baybe import Campaign
        from baybe.recommenders import BotorchRecommender, FPSRecommender, TwoPhaseMetaRecommender

        factor_list = factors_for_requirement(req, factors)
        searchspace = build_searchspace(req, factor_list)
        objective = build_objective(req)
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
    ) -> BaybeRecommendResult:
        if not self.available():
            raise RuntimeError("baybe is not installed (pip install -e '.[baybe,bo,science]')")

        from baybe import Campaign

        measurements = measurements or []
        if campaign_state:
            campaign = Campaign.from_json(campaign_state)
            factor_list = factors_for_requirement(req)
        else:
            campaign, factor_list = self._new_campaign(req)

        metric = primary_metric(req)
        df_meas = records_to_dataframe(measurements, req)
        if not df_meas.empty:
            campaign.add_measurements(df_meas)

        if campaign_state is None and df_meas.empty:
            seed_plan = build_doe_plan(factor_list, "lhs", engine="auto", n=max(batch_size * 2, 8))
            virtual = surrogate_measurements_from_plan(seed_plan, req, metric)
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
    ) -> OptimizationResult:
        """Iterative baybe batch recommendations scored via FormuMind predictor."""
        measurements = list(measurements or [])
        batch_size = max(1, min(4, max(1, iterations // 6)))
        rounds = max(1, iterations // batch_size)
        history: list[float] = []
        best_so_far = float("-inf")
        objectives = req.objectives or default_objectives(req.domain)
        process = process_for(req)
        bounds: dict[str, tuple[float, float]] = {}
        ranked: list[tuple[float, object]] = []
        state = campaign_state
        metric = primary_metric(req)
        settings = get_settings()

        for r in range(rounds):
            result = self.recommend(
                req,
                campaign_state=state,
                measurements=measurements,
                batch_size=batch_size,
                design="baybe_opt",
            )
            state = result.campaign_state

            for run in result.plan.runs:
                form = _score_and_validate(
                    reconstruct.formulation_from_factors(req.domain, run.natural),
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
                measurements.append(
                    ExperimentRecord(
                        domain=req.domain,
                        factors=run.natural,
                        cure_temperature_c=run.natural.get("cure_temperature_c"),
                        measured={metric: form.predicted.get(metric, combined)},
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

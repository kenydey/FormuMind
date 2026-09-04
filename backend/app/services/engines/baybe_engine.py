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


def _rank_by_pareto_then_score(
    ranked: list[tuple[float, object]],
    objectives: list,
    top_n: int,
) -> list[tuple[float, object]]:
    """Order candidates by Pareto front first, weighted score only within a front.

    BayBE optimises a genuine ParetoObjective when there is more than one
    target, and that structure was thrown away here: results were sorted purely
    by the weighted sum, so a candidate that no other dominates could be ranked
    below one that a third candidate beats outright, purely because the weights
    happened to favour it. Sorting by front first keeps the non-dominated set at
    the top and uses the scalar only to break ties inside a front.

    Degrades to the previous scalar ordering when there is one objective or
    fewer than two candidates, where fronts carry no information.
    """
    if len(ranked) < 2 or len(objectives) < 2:
        return sorted(ranked, key=lambda t: t[0], reverse=True)[:top_n]

    from ..tradeoff_analysis import compute_pareto_ranks

    values = [
        [float(getattr(form, "predicted", {}).get(obj.metric, float("nan"))) for obj in objectives]
        for _, form in ranked
    ]
    fronts = compute_pareto_ranks(values, objectives)
    order = sorted(
        range(len(ranked)),
        key=lambda i: (fronts[i] if fronts[i] is not None else 10**6, -ranked[i][0]),
    )
    return [ranked[i] for i in order[:top_n]]


class BaybeCampaignEngine:
    """Recommend next experiments using baybe Campaign + JSON state roundtrip."""

    def available(self) -> bool:
        return baybe_available()

    def _recommender_for(
        self, n_continuous: int, n_objectives: int
    ) -> "tuple":
        """采集超参按复杂度自适应(R2, 2026-09-04)。

        原硬编码 n_restarts=1/n_raw_samples=16 是 2.5min→几秒的时间妥协,
        高维/多目标非线性响应面上收敛性无保障。档位:
          fast(1/16)      — 低维平滑空间(连续因子≤4 且目标≤2), 默认快档;
          balanced(3/32)  — 高维或多目标(默认 auto 在此档);
          thorough(5/64)  — 显式 env FORMUMIND_BO_QUALITY=thorough 才启用
            (4 核 VPS 下单轮可达分钟级, 仅 celery worker 后台可接受)。
        """
        import os

        from baybe.recommenders import BotorchRecommender, FPSRecommender, TwoPhaseMetaRecommender

        quality = os.environ.get("FORMUMIND_BO_QUALITY", "auto").strip().lower()
        if quality == "thorough":
            restarts, raw = 5, 64
        elif quality == "fast":
            restarts, raw = 1, 16
        elif n_continuous > 4 or n_objectives > 2:
            restarts, raw = 3, 32
        else:
            restarts, raw = 1, 16
        return TwoPhaseMetaRecommender(
            initial_recommender=FPSRecommender(),
            recommender=BotorchRecommender(n_restarts=restarts, n_raw_samples=raw),
        )

    def _new_campaign(self, req: Requirement, objectives: list[ObjectiveSpec], factors=None):
        from baybe import Campaign
        from baybe.recommenders import BotorchRecommender, FPSRecommender, TwoPhaseMetaRecommender

        factor_list = factors_for_requirement(req, factors)
        searchspace = build_searchspace(req, factor_list)
        objective = build_objective_from_specs(objectives)
        recommender = self._recommender_for(len(factor_list), len(objectives))
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
        budget_remaining: int | None = None,
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

        # ── KG chemical-compatibility gate (closed-loop constraint) ──────────
        # The whole batch shares one formulation skeleton (material composition
        # from the requirement), so a single KG check covers every run. If the
        # skeleton carries an INHIBITS relation between two resolved materials,
        # every candidate is flagged infeasible with the reason. KG off or no
        # material resolves → no constraint, loop proceeds normally.
        chem_verdict = None
        try:
            from ..kg_chemical_check import check_formulation_chemistry
            from ...domain import knowledge

            skeleton = req.active_formulation or knowledge.baseline_formulation(req)
            if skeleton is not None:
                chk = check_formulation_chemistry(skeleton)
                chem_verdict = {
                    "feasible": chk.feasible,
                    "status": chk.status,
                    "reasons": chk.reasons,
                }
                if not chk.feasible:
                    for run in plan.runs:
                        run.infeasible = True
                        run.infeasible_reason = "; ".join(chk.reasons) or "知识图谱检测到材料不相容"
        except Exception as exc:  # gate must never break recommendation
            log.debug("KG chemical gate skipped ({}); allowing", exc)

        # ── Physical-constraint gate (v11) ───────────────────────────────────
        # Deterministic acid-stability + compliance screen on the same
        # skeleton, stacked AFTER the KG gate. Acid-stability hard hits
        # (strong alkali / carbonate filler / reactive metal in an acidic
        # bath) and RoHS restricted substances mark candidates infeasible;
        # warn-level hits surface as reasons without blocking.
        phys_verdict = None
        try:
            from ..acid_stability import check_acid_stability
            from ..compliance_rules import check_compliance
            from ...domain import knowledge

            skeleton = req.active_formulation or knowledge.baseline_formulation(req)
            if skeleton is not None:
                acid = check_acid_stability(skeleton, bath_ph=req.ph_target)
                comp = check_compliance(skeleton)
                hard_reasons: list[str] = []
                warn_reasons: list[str] = []
                if acid.status == "infeasible":
                    hard_reasons.extend(acid.reasons)
                elif acid.status == "warn":
                    warn_reasons.extend(acid.reasons)
                if comp.status == "infeasible":
                    hard_reasons.extend(comp.reasons)
                elif comp.status == "warn":
                    warn_reasons.extend(comp.reasons)
                phys_verdict = {
                    "feasible": not hard_reasons,
                    "status": "infeasible" if hard_reasons else ("warn" if warn_reasons else "pass"),
                    "reasons": hard_reasons + warn_reasons,
                    "acid_stability": {"status": acid.status, "reasons": acid.reasons},
                    "compliance": {"status": comp.status, "reasons": comp.reasons},
                }
                if hard_reasons:
                    for run in plan.runs:
                        run.infeasible = True
                        run.infeasible_reason = "; ".join(hard_reasons) or "物理约束检测到不可行组合"
        except Exception as exc:  # gate must never break recommendation
            log.debug("Physical-constraint gate skipped ({}); allowing", exc)

        # R2 (2026-09-04): 互斥为成分语义级(骨架成分, 非因子值), 而数值
        # 搜索空间全连续(NumericalContinuousParameter)——BayBE constraints
        # 数学层表达不了跨因子化学互斥, DiscreteExclude 不适用。把"被 gate
        # 拦截的候选占比"写入 notes 成为可度量项: 持续高位(>30%)才值得
        # 研究替代采样(如 genome 空间的候选池裁剪), 不假装能前移。
        n_total = len(plan.runs)
        n_gated = sum(1 for r in plan.runs if getattr(r, "infeasible", False))
        if n_total and n_gated:
            prev = (getattr(plan, "notes", "") or "").strip()
            note = (
                f"gate 拦截 {n_gated}/{n_total} "
                f"({n_gated / n_total * 100:.0f}%)——互斥为成分语义级, "
                "连续因子空间 BayBE constraints 无法数学层表达(见 run.infeasible_reason)"
            )
            plan.notes = f"{prev}; {note}" if prev else note

        result = BaybeRecommendResult(
            plan=plan,
            campaign_state=campaign.to_json(),
            engine="baybe",
            chemical_feasibility=chem_verdict,
            physical_constraints=phys_verdict,
        )
        from ..doe_adaptive import enrich_baybe_result

        all_records = list(measurements)
        if workbench_campaign_id is not None:
            wb_rows = campaign_store.get_experiments_sync(workbench_campaign_id)
            for row in wb_rows:
                if row.measurements:
                    all_records.append(
                        ExperimentRecord(
                            domain=req.domain,
                            factors=dict(row.actual_params or row.planned_params or {}),
                            measured={
                                k: float(v)
                                for k, v in row.measurements.items()
                                if v is not None and v != ""
                            },
                            source="workbench",
                            label=f"wb-{row.id}",
                        )
                    )
        return enrich_baybe_result(result, req, all_records, budget_remaining=budget_remaining)

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
        # Seed the normalisation bounds instead of starting empty. With an
        # empty dict the first batch normalises every metric against a
        # zero-width range, which multi_objective_score reports as the 0.5
        # fallback — so every candidate in round one scored identically
        # regardless of quality, corrupting both the history curve and the
        # initial ranking. The scalar optimiser in workflow.py already seeds
        # this way.
        bounds: dict[str, tuple[float, float]] = predictor.default_bounds(objectives)
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
                run_process = dict(process)
                for k in ("cure_temperature_c", "cure_time_min"):
                    if k in run.natural:
                        run_process[k] = run.natural[k]
                form = _score_and_validate(
                    reconstruct.formulation_from_factors(req, run.natural),
                    run_process,
                    req,
                )
                for m, val in form.predicted.items():
                    lo, hi = bounds.get(m, (val, val))
                    bounds[m] = (min(lo, val), max(hi, val))
                combined = predictor.multi_objective_score(
                    form, objectives, run_process, bounds
                )
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

        top = _rank_by_pareto_then_score(ranked, objectives, settings.top_n_formulas)
        for score, form in top:
            form.name = f"BayBE {req.domain.value} (score {score:.3f})"
        top = [form for _, form in top]

        return OptimizationResult(
            iterations=iterations,
            objective=OBJECTIVE[req.domain],
            objectives=objectives,
            history=history or [0.0],
            top_formulations=top,
            engine="baybe",
        )

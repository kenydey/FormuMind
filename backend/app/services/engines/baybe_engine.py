"""Baybe Campaign engine — stateless via JSON serialization."""
from __future__ import annotations

import logging

from ...domain.objective_contract import align_dataframe_measurement_columns, objective_metrics
from ...domain.schemas import (
    REAL_SOURCES,
    VIRTUAL_SOURCES,
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


def _clean_measurement_dataframe(df, metrics: list[str], expected_params: list[str] | None = None):
    """P1-4: pre-BayBE data cleaning with an explicit, logged strategy.

    Missing data previously flowed into ``campaign.add_measurements`` as NaN
    (or a factor column simply absent, e.g. ``cure_temperature_c``), where
    BayBE/BoTorch failed with an obscure error that surfaced only as the
    "falling back to numpy/optuna" warning. Strategy — no silent imputation,
    inventing optimizer targets is worse than dropping the row:

    - rows whose metrics are ALL NaN → dropped (no signal);
    - rows with SOME NaN metrics → dropped (BayBE cannot fit NaN targets;
      median-fill would fabricate evidence);
    - a factor column required by the searchspace missing from the frame →
      fail-closed ``ValueError`` naming the factors: those measurements
      cannot be placed in this searchspace at all (re-run them, or drop
      them — but not silently).

    Returns ``(cleaned_df, report)``; ``report`` holds dropped/kept counts.
    """

    report = {"dropped_all_nan": 0, "dropped_partial_nan": 0, "kept": 0}
    if df is None or getattr(df, "empty", True):
        return df, report
    if expected_params:
        missing_params = [p for p in expected_params if p not in df.columns]
        if missing_params:
            raise ValueError(
                "测量数据缺失 BayBE 搜索空间要求的因子列 "
                f"{missing_params}（共 {len(df)} 行无法使用）："
                "请补测这些因子后重试，或删除这些历史测量。"
            )
    metric_cols = [m for m in (metrics or []) if m in df.columns]
    if not metric_cols:
        return df, report
    is_nan = df[metric_cols].isna()
    all_nan = is_nan.all(axis=1)
    partial_nan = is_nan.any(axis=1) & ~all_nan
    report["dropped_all_nan"] = int(all_nan.sum())
    report["dropped_partial_nan"] = int(partial_nan.sum())
    cleaned = df.loc[~(all_nan | partial_nan)].reset_index(drop=True)
    report["kept"] = len(cleaned)
    if report["dropped_all_nan"]:
        log.warning(
            "BayBE 测量清洗: 丢弃 %d 行全缺失目标值的测量（无信号）",
            report["dropped_all_nan"],
        )
    if report["dropped_partial_nan"]:
        log.warning(
            "BayBE 测量清洗: 丢弃 %d 行部分缺失目标值的测量（不做填充，避免伪造证据）",
            report["dropped_partial_nan"],
        )
    return cleaned, report


def _prepare_measurement_dataframe(df, metrics: list[str], expected_params: list[str] | None = None):
    if df is None or getattr(df, "empty", True):
        return df
    from ...domain.objective_contract import assert_dataframe_measurement_columns

    aligned = align_dataframe_measurement_columns(df, metrics, log=log)
    assert_dataframe_measurement_columns(aligned, metrics)
    # P1-4: NaN / missing-factor cleaning before BayBE ever sees the frame.
    cleaned, report = _clean_measurement_dataframe(aligned, metrics, expected_params)
    if report["dropped_all_nan"] or report["dropped_partial_nan"]:
        log.info(
            "BayBE 测量清洗报告: kept=%d dropped_all_nan=%d dropped_partial_nan=%d",
            report["kept"], report["dropped_all_nan"], report["dropped_partial_nan"],
        )
    return cleaned


def _dedupe_measurement_frame(df, factor_names: list[str], log):
    """v15: 按因子指纹去重（df_meas 在前，measurements 通道优先保留）。

    消除 measurements 形参通道与 workbench_campaign_id 通道的重复行。
    """
    if df is None or getattr(df, "empty", True):
        return df
    fp_cols = [c for c in factor_names if c in df.columns]
    if not fp_cols:
        return df
    before = len(df)
    out = df.drop_duplicates(subset=fp_cols, keep="first")
    dup = before - len(out)
    if dup:
        log.info(
            "baybe: dropped %d duplicate measurement row(s) "
            "after merging measurements + workbench channels",
            dup,
        )
    return out


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
        seed: int | None = None,
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

        # P0-5: GP 只吃 lab 真实测量；虚拟记录（baybe_opt/predictor_virtual）
        # 不进 GP，只计数披露。无 lab 数据时走冷启动分支。
        _src_counts: dict[str, int] = {}
        for _r in measurements or []:
            _s = getattr(_r, "source", "lab") or "lab"
            _src_counts[_s] = _src_counts.get(_s, 0) + 1
        # v13-3: workbench 真实测量也计入 lab 点数。
        _lab_n = sum(n for s, n in _src_counts.items() if s in REAL_SOURCES)
        # v14-3: 虚拟口径用 VIRTUAL_SOURCES（workbench 不再被双计入）；
        # 未知 source 单独计数披露，防未来新增类型静默归类错误。
        _virtual_n = sum(n for s, n in _src_counts.items() if s in VIRTUAL_SOURCES)
        _unknown_n = sum(
            n for s, n in _src_counts.items()
            if s not in REAL_SOURCES and s not in VIRTUAL_SOURCES
        )
        if _virtual_n or _unknown_n:
            log.info(
                "baybe: %d virtual measurement(s) excluded from GP training "
                "(sources=%s); %d lab measurement(s) used%s",
                _virtual_n, _src_counts, _lab_n,
                f"; {_unknown_n} unknown source(s) ignored" if _unknown_n else "",
            )
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
                # P1-4: pass the searchspace factor names so a measurement
                # frame missing a required factor (e.g. cure_temperature_c)
                # fails closed with a clear message instead of an obscure
                # BayBE error downstream.
                df_wb = _prepare_measurement_dataframe(
                    df_wb, metrics,
                    expected_params=[f.name for f in factor_list],
                )
                df_meas = (
                    pd.concat([df_meas, df_wb], ignore_index=True)
                    if not df_meas.empty
                    else df_wb
                )

        # B-DOE-1: 先清洗再判空。清洗可能删光所有行（目标值全缺），
        # 此时必须走 seed-plan 分支，不能把空帧喂给 add_measurements（BayBE 抛 ValueError）。
        df_meas_clean = (
            _prepare_measurement_dataframe(
                df_meas, metrics,
                expected_params=[f.name for f in factor_list],
            )
            if not df_meas.empty
            else df_meas
        )
        # v15: 双通道归一去重 —— df_meas（measurements 形参）与 df_wb
        # （workbench_campaign_id）可能含同一批行（ingest_workbench_rows 已入库
        # 又经 measurements 传回）。按因子指纹去重，measurements 优先保留，
        # 否则 GP 训练集重复行致后验过度自信。
        df_meas_clean = _dedupe_measurement_frame(
            df_meas_clean, [f.name for f in factor_list], log
        )
        if not df_meas_clean.empty:
            campaign.add_measurements(df_meas_clean)

        if campaign_state is None and df_meas_clean.empty:
            # v13-5: 透传 requirement，冷启动 seed 也过 KG 化学门。
            # v15: 透传 seed，冷启动 LHS 可复现（None 时走 OS 熵，保持旧行为）。
            seed_plan = build_doe_plan(
                factor_list, "lhs", engine="auto", n=max(batch_size * 2, 8),
                requirement=req, seed=seed,
            )
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
            log.debug("KG chemical gate skipped (%s); allowing", exc)

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
            log.debug("Physical-constraint gate skipped (%s); allowing", exc)

        # R2/P2: 数值空间全连续 → DiscreteExclude 仍不适用, gate 占比可度量。
        # genome 路径的 categorical mat_* 已注入 DiscreteExcludeConstraint
        # (acid/metal/carbonate/alkali 高频互斥); 此处度量覆盖连续空间残差。
        n_total = len(plan.runs)
        n_gated = sum(1 for r in plan.runs if getattr(r, "infeasible", False))
        if n_total and n_gated:
            prev = (getattr(plan, "notes", "") or "").strip()
            note = (
                f"gate 拦截 {n_gated}/{n_total} "
                f"({n_gated / n_total * 100:.0f}%)——连续因子空间残差互斥仍靠 "
                "post-hoc gate; genome categorical 已 DiscreteExclude 前移"
                "(见 run.infeasible_reason)"
            )
            plan.notes = f"{prev}; {note}" if prev else note

        result = BaybeRecommendResult(
            plan=plan,
            campaign_state=campaign.to_json(),
            engine="baybe",
            chemical_feasibility=chem_verdict,
            physical_constraints=phys_verdict,
            # v15: 实际喂给 add_measurements 的 lab 点数（去重后，含 df_wb 通道）
            lab_points_used=len(df_meas_clean),
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
        # v15: 聚合各轮实际喂给 GP 的 lab 点数
        _max_lab_points = 0

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
            # v15: 聚合各轮实际喂给 GP 的 lab 点数
            _max_lab_points = max(_max_lab_points, result.lab_points_used)

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
                        # v9: run.natural 是 dict[str, float|str]，str 离散因子
                        # 传给 float|None 字段会 ValidationError —— 非数值时记 None。
                        cure_temperature_c=(
                            _ct
                            if isinstance(
                                (_ct := run.natural.get("cure_temperature_c")), (int, float)
                            )
                            else None
                        ),
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

        # U-2: 血缘打通 —— 有真实 lab 测量 seed 时不再谎报 predictor_virtual。
        from ..doe_cycle_service import lab_measurement_source

        # P0-5: 披露实际进入 GP 的 lab 点数（baybe_opt 虚拟点不进 GP）。
        # v13-3: workbench 真实测量也计入。
        # v15: 以各轮 recommend() 实际喂给 GP 的 lab 点数（取最大值）为事实源。
        _src = (
            "lab"
            if _max_lab_points > 0
            else lab_measurement_source(measurements or [])
        )
        return OptimizationResult(
            iterations=iterations,
            objective=OBJECTIVE[req.domain],
            objectives=objectives,
            history=history or [0.0],
            top_formulations=top,
            engine="baybe",
            measurement_source=_src,
            # P0-5: 披露实际进入 GP 的 lab 点数（虚拟点已被过滤）。
            lab_points_used=_max_lab_points,
        )

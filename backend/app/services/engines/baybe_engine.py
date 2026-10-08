"""Baybe Campaign engine — stateless via JSON serialization."""
from __future__ import annotations

import contextlib
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
from .adapters.baybe_objective_builder import build_objective_from_specs
from .adapters.baybe_space_builder import build_searchspace, factors_for_requirement, factors_from_campaign
from .adapters.doe_adapter import dataframe_to_doe_plan
from .adapters.measurements_adapter import records_to_dataframe, surrogate_measurements_from_plan
from .campaign_objectives import resolve_campaign_objectives
from .doe_registry import baybe_available, build_doe_plan

log = logging.getLogger(__name__)


@contextlib.contextmanager
def _seeded_rng(seed: int | None):
    """v27 P3-27: 播种 torch/numpy 全局 RNG，退出时恢复原状态。

    替代 v18-6 的手工 save/restore —— 原写法在冷启动早期返回点
    （return alt_plan.runs / return []）泄漏，污染调用方 RNG。
    try/finally 保证任何返回路径（含异常）都恢复。
    """
    _torch_state, _np_state = None, None
    if seed is None:
        yield
        return
    try:
        import torch

        _torch_state = torch.get_rng_state()
        torch.manual_seed(seed)
    except Exception:  # noqa: BLE001
        pass
    try:
        import numpy as np

        _np_state = np.random.get_state()
        np.random.seed(seed % (2**32))
    except Exception:  # noqa: BLE001
        pass
    try:
        yield
    finally:
        if _torch_state is not None:
            try:
                import torch

                torch.set_rng_state(_torch_state)
            except Exception:  # noqa: BLE001
                pass
        if _np_state is not None:
            try:
                import numpy as np

                np.random.set_state(_np_state)
            except Exception:  # noqa: BLE001
                pass


def _fp_val(x):
    """v27 P2-18: 测量行指纹的逐值格式化 —— 不按列 dtype 分支。

    object 列（含 None 的测量列常见）此前走 ``astype(str)``，
    ``round(6)`` 归一化被跳过，``0.1+0.2`` vs ``0.3`` 去重 miss。
    bool 优先于 int/float 判断（bool 是 int 子类）；NaN 经 f-string 得 "nan"。
    """
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, (int, float)):
        return f"{round(float(x), 6):.6f}"
    try:
        import numpy as _np

        if isinstance(x, _np.bool_):
            return str(bool(x))
        if isinstance(x, (_np.integer, _np.floating)):
            return f"{round(float(x), 6):.6f}"
    except ImportError:
        pass
    return str(x)


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


def _fps_select(df, k: int) -> list[int]:
    """v16 P3-14: Farthest Point Sampling —— 从 df 选 k 个在因子空间最分散的行。

    用于冷启动先验点的多样性选点。fail-open：异常时返回前 k 个索引。
    """
    try:
        import numpy as np

        vals = df.select_dtypes(include=[np.number]).to_numpy(dtype=float)
        if vals.shape[0] == 0 or vals.shape[1] == 0:
            return list(range(min(k, len(df))))
        # 标准化到 [0,1]
        lo = vals.min(axis=0)
        hi = vals.max(axis=0)
        span = np.where(hi > lo, hi - lo, 1.0)
        norm = (vals - lo) / span
        n = len(norm)
        k = min(k, n)
        selected = [0]
        min_dist = np.full(n, np.inf)
        for _ in range(1, k):
            last = norm[selected[-1]]
            d = np.linalg.norm(norm - last, axis=1)
            min_dist = np.minimum(min_dist, d)
            min_dist[selected] = -1  # 已选不再选
            selected.append(int(np.argmax(min_dist)))
        return selected
    except Exception:  # noqa: BLE001 - fail-open
        return list(range(min(k, len(df))))


def _dedupe_measurement_frame(df, factor_names: list[str], log, metric_names: list[str] | None = None):
    """v15: 按因子+指标指纹去重（df_meas 在前，measurements 通道优先保留）。

    消除 measurements 形参通道与 workbench_campaign_id 通道的重复行。
    v16: 指纹纳入指标列 —— 同一因子组合的多次独立测量（估计纯误差的常规
    DOE 手段）测量值不同，不再被误杀；只有因子+指标全相同的真重复行才去重。
    """
    if df is None or getattr(df, "empty", True):
        return df
    fp_cols = [c for c in factor_names if c in df.columns]
    # v16: 指标列纳入指纹，保护真实重复实验
    for m in metric_names or []:
        if m in df.columns and m not in fp_cols:
            fp_cols.append(m)
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
        # v27 P3-27: RNG 播种/恢复走 _seeded_rng 上下文管理器 —— try/finally
        # 保证冷启动早期返回也不泄漏种子。原 v18-6 手工写法在
        # return alt_plan.runs / return [] 处跳过恢复。
        with _seeded_rng(seed):
            return self._recommend_inner(
                req,
                campaign_state=campaign_state,
                measurements=measurements,
                batch_size=batch_size,
                design=design,
                workbench_campaign_id=workbench_campaign_id,
                store=store,
                budget_remaining=budget_remaining,
                seed=seed,
            )

    def _recommend_inner(
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

        # P0-5: GP 只吃 lab 真实测量；外部虚拟记录（predictor_virtual）不进 GP。
        # v18-1: baybe_opt 是内部循环反馈，必须进 GP（否则多轮 GP 冻结）。
        _src_counts: dict[str, int] = {}
        for _r in measurements or []:
            _s = getattr(_r, "source", "lab") or "lab"
            _src_counts[_s] = _src_counts.get(_s, 0) + 1
        # v13-3: workbench 真实测量也计入 lab 点数。
        _lab_n = sum(n for s, n in _src_counts.items() if s in REAL_SOURCES)
        # v14-3: 虚拟口径用 VIRTUAL_SOURCES（workbench 不再被双计入）；
        # v18-1: baybe_opt 已进 GP，不再计入"被排除"。
        # 未知 source 单独计数披露，防未来新增类型静默归类错误。
        _virtual_n = sum(
            n for s, n in _src_counts.items()
            if s in VIRTUAL_SOURCES and s != "baybe_opt"
        )
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
        df_meas = records_to_dataframe(
            measurements, req, objectives,
            # v18-1: 内部循环反馈 —— 本轮产生的 baybe_opt 记录必须进 GP，
            # 否则多轮优化的 GP 后验冻结（"迭代"名存实亡）。
            # 与外部传入的虚拟记录区分：baybe_opt 是内部反馈源。
            include_sources=REAL_SOURCES | {"baybe_opt"},
        )
        if not df_meas.empty and metrics:
            df_meas = align_dataframe_measurement_columns(df_meas, metrics, log=log)

        if workbench_campaign_id is not None:
            actual_X, measurements_Y = fetch_campaign_data_for_baybe(
                workbench_campaign_id, req, store=campaign_store
            )
            df_wb = workbench_dataframes_to_baybe(actual_X, measurements_Y, metrics)
            if not df_wb.empty:
                # v16 P2-3: 日志移到去重后 —— 去重前计数与实际进 GP 的行数不一致。
                # P1-4: pass the searchspace factor names so a measurement
                # frame missing a required factor (e.g. cure_temperature_c)
                # fails closed with a clear message instead of an obscure
                # BayBE error downstream.
                df_wb = _prepare_measurement_dataframe(
                    df_wb, metrics,
                    expected_params=[f.name for f in factor_list],
                )
                import pandas as pd

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
        # 又经 measurements 传回）。按因子+指标指纹去重，measurements 优先保留，
        # 否则 GP 训练集重复行致后验过度自信。
        df_meas_clean = _dedupe_measurement_frame(
            df_meas_clean, [f.name for f in factor_list], log,
            metric_names=metrics,
        )
        # v16 P2-3: 去重后日志（计数与实际进 GP 一致）。
        if workbench_campaign_id is not None:
            log.info(
                "Workbench measurements for campaign %s: metrics=%s rows=%d (post-dedupe)",
                workbench_campaign_id,
                metrics,
                len(df_meas_clean),
            )
        if not df_meas_clean.empty:
            # v17-1: 跨轮去重 —— campaign_state 反序列化已含上一轮测量，
            # 全量 measurements 会重复添加。用因子+指标指纹过滤已存在行。
            if campaign_state is not None:
                try:
                    existing = campaign.measurements
                    if existing is not None and not existing.empty:
                        fp_cols = [c for c in df_meas_clean.columns if c in existing.columns]
                        if fp_cols:
                            # v24-fix: round(6) 后转 str，避免跨 pandas/numpy 版本浮点格式差异导致指纹 miss。
                            # v27 P2-18: 用模块级 _fp_val 逐值格式化（见上）。
                            def _fp_str(df):
                                return df[fp_cols].apply(lambda col: col.map(_fp_val))
                            existing_fp = set(map(tuple, _fp_str(existing).values.tolist()))
                            mask = ~_fp_str(df_meas_clean).apply(tuple, axis=1).isin(existing_fp)
                            df_meas_clean = df_meas_clean[mask]
                            if df_meas_clean.empty:
                                log.info("baybe: all measurements already in campaign, skipping add")
                except Exception:
                    pass  # fail-open: 指纹过滤失败则按原逻辑添加
            if not df_meas_clean.empty:
                campaign.add_measurements(df_meas_clean)

        if campaign_state is None and df_meas_clean.empty:
            # v13-5: 透传 requirement，冷启动 seed 也过 KG 化学门。
            # v15: 透传 seed，冷启动 LHS 可复现（None 时用确定性默认 0，与 DOE 链一致）。
            seed_plan = build_doe_plan(
                factor_list, "lhs", engine="auto", n=max(batch_size * 2, 8),
                requirement=req, seed=seed,
            )
            virtual = surrogate_measurements_from_plan(seed_plan, req, None)
            if not virtual.empty and metrics:
                virtual = align_dataframe_measurement_columns(virtual, metrics, log=log)
            if not virtual.empty:
                # v16 P3-14: FPS 多样性选点（替代 head(3) 的顺序截断）——
                # 冷启动先验点应在因子空间均匀散布，而非取 LHS 的前 3 个。
                # TODO(P3-14): 先验仍与 predictor 同源；理想是无信息 prior。
                try:
                    _fps_idx = _fps_select(virtual, min(3, len(virtual)))
                    virtual = virtual.iloc[_fps_idx]
                except Exception:  # noqa: BLE001 - fail-open
                    virtual = virtual.head(min(3, len(virtual)))
                campaign.add_measurements(virtual)

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
            # v19-fix: 只计 REAL_SOURCES，不含 baybe_opt（v18-1 回归修复）。
            # _lab_n 是去重前计数，用于 measurement_source 诚实性判定已足够。
            lab_points_used=_lab_n,
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
        # v16 P2-9: BayBE 替换池恒空 —— 用 LHS 从因子范围补采样，保证 batch 恒满。
        def _baybe_resample(n: int):
            from .doe_registry import build_doe_plan
            from ...pipeline.workflow import build_doe_factors

            try:
                factors = build_doe_factors(req)
                # v19-4: 透传 seed，补齐 seed 链（seed=None 时 native 内部用 0，确定性）。
                alt_plan = build_doe_plan(factors, "lhs", engine="native", n=n, seed=seed)
                return alt_plan.runs
            except Exception:  # noqa: BLE001 - fail-open
                return []

        return enrich_baybe_result(
            result, req, all_records,
            budget_remaining=budget_remaining,
            resample_fn=_baybe_resample,
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
        seed: int | None = None,
    ) -> OptimizationResult:
        """Iterative baybe batch recommendations scored via FormuMind predictor.

        seed: None = OS entropy (historical default); int = reproducible.
        Multi-round campaigns use ``seed + round_index`` per round.
        """
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
        # v23-fix: 删除死代码（metric 赋值后无引用，且 primary_metric 用裸 objectives）。
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
                seed=None if seed is None else seed + r,
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
                # v21-fix: 缺 key 时写 None（下游 B-DOE-1 清洗处理），
                # 不用 combined（[0,1] 归一化分）或 0.0 填充 —— 错误量纲毒化 GP。
                # 与 measurements_adapter 的 "skip 而非 0.0" 原则对齐。
                measured_vals = {m: form.predicted.get(m) for m in objective_metric_names}
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

            # v18-7: 收敛早停 —— 目标达成即停，节省 GP 采样与 predictor 计算。
            # v19-fix: 用原始 metric 值判收敛（best_so_far 是归一化 [0,1] 分，
            # 与 target 的原始量纲不可比 —— v18 量纲 bug）。
            try:
                from ..convergence import target_achieved

                _obj0 = objectives[0] if objectives else None
                if _obj0 is not None and ranked:
                    _top_form = max(ranked, key=lambda t: t[0])[1]
                    _raw_val = (_top_form.predicted or {}).get(_obj0.metric)
                    if target_achieved(_raw_val, _obj0):
                        log.info(
                            "baybe: target achieved at round %d/%d (%s=%.3f), early stop",
                            r + 1, rounds, _obj0.metric, _raw_val,
                        )
                        break
            except Exception:  # noqa: BLE001
                pass

        top = _rank_by_pareto_then_score(ranked, objectives, settings.top_n_formulas)
        for score, form in top:
            # v17 CI-5: name 用 form.score（真分数），而非 ranking combined 分数 ——
            # test_optimization_top_names_match_true_scores 要求 name 与真分数一致，
            # test_pipeline 要求 form.score 为真实目标值。两者不能混用。
            form.name = f"BayBE {req.domain.value} (score {form.score:.3f})"
        top = [form for _, form in top]

        # U-2: 血缘打通 —— 有真实 lab 测量 seed 时不再谎报 predictor_virtual。
        from ..doe_cycle_service import lab_measurement_source

        # P0-5: 披露实际进入 GP 的 lab 点数。
        # v18-1: baybe_opt 进 GP（内部反馈）；v19-3: lab_points_used 只计 REAL_SOURCES。
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

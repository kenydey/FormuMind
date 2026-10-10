"""Explain adaptive DOE recommendations (P1).

Generates human-readable rationale for each suggested run without replacing
BayBE / legacy acquisition logic.
"""
from __future__ import annotations

import math
from typing import Literal

from ..domain.project_spec import primary_objective
from ..domain.schemas import DOEPlan, ExperimentRecord, Requirement, RunExplanation


def experiment_id(exp: ExperimentRecord, idx: int) -> str:
    if exp.label:
        return exp.label
    return f"record-{idx}"


def infer_strategy(
    n_completed: int,
    *,
    budget_remaining: int | None = None,
) -> tuple[Literal["exploration", "balanced", "exploitation"], str]:
    """Map completed experiment count (+ optional budget) to a strategy label."""
    if n_completed < 8:
        label: Literal["exploration", "balanced", "exploitation"] = "exploration"
        rationale = f"已完成 {n_completed} 次实验，数据量较少，以空间探索为主。"
    elif n_completed < 20:
        label = "balanced"
        rationale = f"已完成 {n_completed} 次实验，在探索新区域与优化已知高性能区之间平衡。"
    else:
        label = "exploitation"
        rationale = f"已完成 {n_completed} 次实验，数据较充分，以利用模型后验、聚焦最优区为主。"

    if budget_remaining is not None and budget_remaining < 5:
        rationale += f" 剩余预算仅 {budget_remaining} 次，建议优先验证高置信候选点。"
        if label == "exploration":
            label = "balanced"

    return label, rationale


# Nearest-neighbour RMS distance (range-normalised, so in [0, 1]) above which a
# candidate counts as sitting in unexplored space.
_SPARSE_DISTANCE = 0.35


def factor_spans(points: list[dict[str, float | str]]) -> dict[str, float]:
    """Observed range of every numeric factor across *points* (for normalising)."""
    lo: dict[str, float] = {}
    hi: dict[str, float] = {}
    for p in points:
        for k, v in (p or {}).items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            lo[k] = min(lo.get(k, v), v)
            hi[k] = max(hi.get(k, v), v)
    return {k: hi[k] - lo[k] for k in lo}


def _factor_distance(
    a: dict[str, float | str],
    b: dict[str, float | str],
    spans: dict[str, float] | None = None,
) -> float:
    """RMS per-factor distance, each factor scaled by its observed range.

    The old span was ``max(a, b) - min(a, b)`` — the gap between the two points
    themselves — so every unequal pair contributed exactly 1 whatever the real
    separation, and the distance only ever counted *how many* factors differed.
    With ``spans`` (see :func:`factor_spans`) a factor contributes
    ``((a-b)/range)**2`` in [0, 1].
    """
    keys = set(a) & set(b)
    if not keys:
        return float("inf")
    dist = 0.0
    for key in keys:
        av, bv = a[key], b[key]
        # v8: 离散 str 因子用 0/1 示性距离，不参与欧氏计算
        if isinstance(av, str) or isinstance(bv, str):
            dist += 0.0 if av == bv else 1.0
            continue
        span = (spans or {}).get(key) or 0.0
        if span <= 0:
            span = max(abs(av), abs(bv), 1.0)
        dist += ((av - bv) / span) ** 2
    return math.sqrt(dist / len(keys))


def k_nearest_experiments(
    factors: dict[str, float],
    existing: list[ExperimentRecord],
    *,
    k: int = 2,
) -> list[tuple[ExperimentRecord, str]]:
    if not existing:
        return []
    scored: list[tuple[float, ExperimentRecord, str]] = []
    spans = factor_spans([factors] + [e.factors or {} for e in existing])
    for idx, exp in enumerate(existing):
        dist = _factor_distance(factors, exp.factors or {}, spans)
        scored.append((dist, exp, experiment_id(exp, idx)))
    scored.sort(key=lambda t: t[0])
    return [(exp, eid) for _, exp, eid in scored[:k]]


def _describe_region(natural: dict[str, float], plan: DOEPlan) -> str:
    parts: list[str] = []
    for factor in plan.factors[:3]:
        val = natural.get(factor.name)
        if val is None:
            continue
        if isinstance(val, str):  # discrete level: no low/mid/high band
            parts.append(f"{factor.name}（{val}）")
            continue
        if val <= factor.low + (factor.high - factor.low) * 0.33:
            band = "低"
        elif val >= factor.low + (factor.high - factor.low) * 0.67:
            band = "高"
        else:
            band = "中"
        unit = f" {factor.unit}" if factor.unit else ""
        parts.append(f"{factor.name} {band}（{val:.2g}{unit}）")
    return "、".join(parts) if parts else "未覆盖因子空间"


def _y_best(existing: list[ExperimentRecord], metric: str, direction: str = "maximize") -> float:
    # P1-3: minimize 方向取 min（之前恒用 max，minimize 目标全错）
    if not existing:
        return 0.0
    vals = [(exp.measured or {}).get(metric, 0.0) for exp in existing]
    return min(vals) if direction == "minimize" else max(vals)


def _estimate_improvement_pct(mean: float, y_best: float, direction: str = "maximize") -> float:
    if y_best <= 0:
        return 0.0
    # P1-3: minimize 时改进 = (y_best - mean) / y_best
    if direction == "minimize":
        return max(0.0, (y_best - mean) / y_best * 100.0)
    return max(0.0, (mean - y_best) / y_best * 100.0)


def _run_strategy(
    strategy_label: Literal["exploration", "balanced", "exploitation"],
    *,
    is_sparse: bool,
) -> Literal["exploration", "exploitation", "balanced", "constraint_fill"]:
    if is_sparse:
        return "exploration"
    if strategy_label == "exploration":
        return "exploration"
    if strategy_label == "exploitation":
        return "exploitation"
    return "balanced"


def build_run_explanations(
    req: Requirement,
    plan: DOEPlan,
    existing: list[ExperimentRecord],
    *,
    strategy_label: Literal["exploration", "balanced", "exploitation"],
    acquisition_scores: dict[int, float] | None = None,
    constraint_warnings_by_run: dict[int, list[str]] | None = None,
) -> list[RunExplanation]:
    """Build one explanation per AI-suggested run in *plan*."""
    from .active_learning import _ei_acquisition, _surrogate_score
    from .convergence import primary_objective_spec

    metric = primary_objective(req)
    # P1-3: 取 direction，minimize 目标不再用 maximize 逻辑
    _spec = primary_objective_spec(req)
    direction = str(getattr(_spec, "direction", "maximize") or "maximize").lower()
    domain = plan.domain or req.domain
    y_best = _y_best(existing, metric, direction)
    suggested = [r for r in plan.runs if r.ai_suggested] or plan.runs
    explanations: list[RunExplanation] = []

    for run in suggested:
        nearest = k_nearest_experiments(run.natural, existing, k=2)
        nearest_ids = [eid for _, eid in nearest]
        is_sparse = not nearest or _factor_distance(
            run.natural,
            nearest[0][0].factors or {},
            factor_spans([run.natural] + [e.factors or {} for e in existing]),
        ) > _SPARSE_DISTANCE
        run_strategy = _run_strategy(strategy_label, is_sparse=is_sparse)
        acq = (acquisition_scores or {}).get(run.run_id)
        cw = (constraint_warnings_by_run or {}).get(run.run_id, [])

        if acq is None and domain is not None:
            mean, std = _surrogate_score(run.natural, domain, existing, metric)
            acq = _ei_acquisition(mean, std, y_best, direction=direction)

        if run_strategy == "exploration" or is_sparse:
            summary = f"探索 {_describe_region(run.natural, plan)} 区域（当前数据稀疏）"
        else:
            mean, _ = _surrogate_score(run.natural, domain, existing, metric)
            delta = _estimate_improvement_pct(mean, y_best, direction)
            ref = "、".join(nearest_ids) if nearest_ids else "无"
            summary = f"预计 {metric} 提升约 {delta:.1f}%（参考实验：{ref}）"

        if cw:
            run_strategy = "constraint_fill"
            summary += f"；约束提示：{cw[0]}"

        explanations.append(
            RunExplanation(
                run_id=run.run_id,
                strategy=run_strategy,
                summary=summary,
                nearest_experiment_ids=nearest_ids,
                predicted_delta_pct=None if run_strategy == "exploration" else _estimate_improvement_pct(
                    _surrogate_score(run.natural, domain, existing, metric)[0], y_best, direction
                ),
                acquisition_score=round(acq, 4) if acq is not None else None,
                constraint_warnings=cw,
            )
        )

    return explanations


def recommend_next_action(
    *,
    n_completed: int,
    strategy_label: Literal["exploration", "balanced", "exploitation"],
    budget_remaining: int | None,
    anomalies: list,
) -> str:
    if anomalies:
        critical = [a for a in anomalies if getattr(a, "severity", "") == "critical"]
        if critical:
            return f"发现 {len(critical)} 个严重异常点，建议先复测后再继续推荐。"
        return f"发现 {len(anomalies)} 个可疑实验点，建议核对测量数据。"

    if budget_remaining is not None and budget_remaining <= 0:
        return "实验预算已用尽，建议汇总结果并进入优化/报告阶段。"

    if strategy_label == "exploration":
        return "继续执行推荐批次以覆盖因子空间；完成 ≥8 次后可切换到平衡策略。"
    if strategy_label == "exploitation":
        return "聚焦最优区验证模型预测；若 RMSE 已收敛可考虑停止新增实验。"
    return "在探索与利用之间交替推进；关注模型 RMSE 与 Pareto 前沿变化。"


def legacy_acquisition_scores(
    plan: DOEPlan,
    existing: list[ExperimentRecord],
    *,
    n_suggest: int,
    objective_metric: str | None = None,
    # P1-3: direction 参数，minimize 目标不再用 maximize EI
    direction: str = "maximize",
) -> dict[int, float]:
    """Recompute EI scores for legacy active-learning suggested runs."""
    from ..pipeline.workflow import OBJECTIVE
    from .active_learning import _ei_acquisition, _surrogate_score

    if plan.domain is None:
        return {}
    obj_metric = objective_metric or OBJECTIVE.get(plan.domain, "salt_spray_hours")
    y_best = _y_best(existing, obj_metric, direction)
    scores: dict[int, float] = {}
    for run in plan.runs:
        if not run.ai_suggested:
            continue
        mean, std = _surrogate_score(run.natural, plan.domain, existing, obj_metric)
        scores[run.run_id] = _ei_acquisition(mean, std, y_best, direction=direction)
    return scores

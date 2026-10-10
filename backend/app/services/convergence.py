"""B-7: 收敛判定统一入口。

``auto_loop``（闭环迭代）、``workbench_loop``（工作台闭环任务经由
``auto_loop.loop_iterate``）与 ``doe_cycle_service``（DOE 周期硬停）此前
各有一份收敛判定逻辑（RMSE 平台期 / 目标达成 / 最优测量值提取），
现统一收敛到本模块。调用方只从这里 import。

约定：
- ``evaluate_convergence`` 返回 ``(converged, reason)``，
  ``reason`` ∈ ``{"target_achieved", "rmse_plateau", ""}``；
  （``doe_cycle_runs.convergence_reason`` 列还可能有 ``"budget_exhausted"``，
  那是 DOE 周期硬停写入的，不经过本模块。）
- 目标达成优先于平台期（与原 auto_loop 语义一致）；
- ``None`` 数据永不判收敛（fail-open）。
"""
from __future__ import annotations

from ..domain.schemas import ObjectiveSpec, Requirement

#: 收敛原因常量（与 doe_cycle_runs.convergence_reason 列取值一致）。
REASON_TARGET_ACHIEVED = "target_achieved"
REASON_RMSE_PLATEAU = "rmse_plateau"
REASON_NONE = ""

#: plateau 检测只看近 N 轮，避免跨长循环无限增长内存（S7）。
_PLATEAU_HISTORY_CAP = 50


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


def target_achieved(best_so_far: float | None, objective: ObjectiveSpec) -> bool:
    """P2-4: True when the measured best already meets the objective target.

    Pure function. ``None`` best or ``None`` target → False (fail-open:
    never claim convergence without data).
    """
    target = objective.target_value
    if best_so_far is None or target is None:
        return False
    if objective.direction == "minimize":
        return best_so_far <= target
    return best_so_far >= target


def best_objective_value(
    records: list | None, metric: str, direction: str = "maximize"
) -> float | None:
    """P2-4: best measured value of ``metric`` across prior records."""
    vals: list[float] = []
    for r in records or []:
        v = (getattr(r, "measured", None) or {}).get(metric)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            vals.append(float(v))
    if not vals:
        return None
    return min(vals) if direction == "minimize" else max(vals)


def primary_objective_spec(req: Requirement) -> ObjectiveSpec:
    """Primary objective: req.objectives[0], else a target-less default.

    注意：收敛判定只看主目标（objectives[0]）。其余目标的 target_value
    不参与收敛判定（多目标权衡走 Pareto/加权分，不走收敛门）。
    """
    from ..domain.project_spec import primary_objective

    objectives = getattr(req, "objectives", None) or []
    if objectives:
        return objectives[0]
    return ObjectiveSpec(metric=primary_objective(req))


def evaluate_convergence(
    *,
    prior_rmse_history: list[dict[str, float]] | None,
    current_rmse: dict[str, float] | None,
    records: list | None,
    objective: ObjectiveSpec,
    enabled: bool,
    eps: float,
    patience: int,
) -> tuple[bool, str]:
    """统一收敛判定：目标达成（P2-4）或 RMSE 平台期。

    Returns (converged, reason); reason ∈
    {"target_achieved", "rmse_plateau", ""}.
    """
    if not enabled:
        return False, REASON_NONE
    # 仅用于 plateau 检测：近 N 轮足够，避免跨长循环无限增长内存（S7）。
    history = list(prior_rmse_history or [])[-_PLATEAU_HISTORY_CAP:]
    full_history = history + [current_rmse] if current_rmse else history
    best_measured = best_objective_value(
        records, objective.metric, objective.direction
    )
    if target_achieved(best_measured, objective):
        return True, REASON_TARGET_ACHIEVED
    if current_rmse and rmse_plateau_detected(
        full_history, eps=eps, patience=patience
    ):
        return True, REASON_RMSE_PLATEAU
    return False, REASON_NONE

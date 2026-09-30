"""B-7: services/convergence.py 统一收敛判定入口。"""
from __future__ import annotations

import pytest

from app.domain.schemas import ObjectiveSpec
from app.services.convergence import (
    best_objective_value,
    evaluate_convergence,
    rmse_plateau_detected,
    target_achieved,
)


def _obj(target=1000.0, direction="maximize"):
    return ObjectiveSpec(metric="m", target_value=target, direction=direction)


def _rec(val):
    from types import SimpleNamespace

    return SimpleNamespace(measured={"m": val})


# ── evaluate_convergence ──────────────────────────────────────────

BASE = dict(
    prior_rmse_history=[{"m": 1.0}, {"m": 1.0}, {"m": 1.0}],
    current_rmse={"m": 1.0},
    records=[],
    objective=_obj(),
    enabled=True,
    eps=0.01,
    patience=2,
)


def test_disabled_never_converges():
    converged, reason = evaluate_convergence(**{**BASE, "enabled": False})
    assert (converged, reason) == (False, "")


def test_target_achieved_wins_over_plateau():
    # 即使 RMSE 也进入平台期，目标达成的原因优先。
    converged, reason = evaluate_convergence(
        **{**BASE, "records": [_rec(1200.0)]}
    )
    assert (converged, reason) == (True, "target_achieved")


def test_plateau_when_no_target_hit():
    converged, reason = evaluate_convergence(**BASE)
    assert (converged, reason) == (True, "rmse_plateau")


def test_improving_rmse_not_converged():
    kw = dict(BASE)
    kw["prior_rmse_history"] = [{"m": 2.0}, {"m": 1.5}, {"m": 1.0}]
    kw["current_rmse"] = {"m": 0.5}
    assert evaluate_convergence(**kw) == (False, "")


def test_none_data_fail_open():
    kw = dict(BASE)
    kw["current_rmse"] = None
    kw["prior_rmse_history"] = []
    assert evaluate_convergence(**kw) == (False, "")


def test_target_without_target_value_fail_open():
    obj = ObjectiveSpec(metric="m")  # target_value=None
    kw = dict(BASE, objective=obj, records=[_rec(9999.0)])
    # RMSE 仍是平台期 → 平台期原因（目标无阈值不判达成）
    assert evaluate_convergence(**kw) == (True, "rmse_plateau")


def test_long_history_truncated_safely():
    # 60 轮历史：前段改善、近段平台 → 仍判平台期（只看近 50 轮）。
    hist = [{"m": 5.0 - 0.1 * i} for i in range(57)] + [
        {"m": 1.0},
        {"m": 1.0},
        {"m": 1.0},
    ]
    kw = dict(BASE, prior_rmse_history=hist, current_rmse={"m": 1.0})
    assert evaluate_convergence(**kw) == (True, "rmse_plateau")


# ── 旧入口仍可用（re-export 兼容） ─────────────────────────────────

def test_auto_loop_reexports_still_work():
    from app.services import auto_loop

    assert auto_loop.rmse_plateau_detected is rmse_plateau_detected
    assert auto_loop.target_achieved is target_achieved
    assert auto_loop.best_objective_value is best_objective_value


def test_doe_cycle_service_imports_from_convergence():
    import inspect

    import app.services.doe_cycle_service as dcs

    src = inspect.getsource(dcs)
    assert "from .convergence import" in src
    assert "from .auto_loop import" not in src

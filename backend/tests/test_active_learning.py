"""P4: active_learning 伪不确定度自适应测试(2026-09-04)。"""
from __future__ import annotations

import pytest

from app.domain.schemas import ExperimentRecord, Measurement


def _mk_rec(metric: str, value: float) -> ExperimentRecord:
    return ExperimentRecord(
        domain="autodeposition_coating",
        measurements=[Measurement(metric=metric, value=value)],
        source="lab",
    )


@pytest.fixture()
def force_empirical_branch(monkeypatch):
    """registry.predict_with_std → None → _surrogate_score 落到经验伪方差分支。"""
    monkeypatch.setattr(
        "app.services.training.registry.predict_with_std", lambda *a, **k: None
    )
    import app.services.active_learning as al

    return al


def test_tiny_scale_no_absolute_floor_dominance(force_empirical_branch, monkeypatch):
    """量纲极小(mean=1e-4): 旧实现 std≈1e-3(绝对地板主导, std≫signal);
    新实现地板相对化 → std≈2.5e-5, 不再被绝对常数淹没。"""
    al = force_empirical_branch
    monkeypatch.setattr(
        "app.services.predictor.predict", lambda form: {"salt_spray_hours": 1e-4}
    )
    mean, std = al._surrogate_score({}, "autodeposition_coating", [], "salt_spray_hours")
    assert mean == pytest.approx(1e-4)
    assert std < 1e-4  # 旧实现 ≥1e-3; 新实现低于 signal 量纲
    assert std == pytest.approx(1e-4 * 0.20 + max(1e-4 * 0.05, 1e-6), rel=0.3)


def test_measured_scatter_used_when_three_records(force_empirical_branch, monkeypatch):
    """同属性 ≥3 条实测 → std 用实测离散度 pstdev, 非固定比例。"""
    al = force_empirical_branch
    monkeypatch.setattr(
        "app.services.predictor.predict", lambda form: {"salt_spray_hours": 0.5}
    )
    recs = [_mk_rec("salt_spray_hours", v) for v in (0.10, 0.12, 0.14)]
    mean, std = al._surrogate_score({}, "autodeposition_coating", recs, "salt_spray_hours")
    assert std == pytest.approx(0.0163, abs=0.002)  # pstdev(0.10,0.12,0.14)
    assert mean == pytest.approx(0.5)  # mean 仍来自 predictor


def test_no_metric_in_predictor_returns_zero(force_empirical_branch, monkeypatch):
    """目标属性不在预测器输出 → (0,0)(调用方跳过, 语义保留)。"""
    al = force_empirical_branch
    monkeypatch.setattr(
        "app.services.predictor.predict", lambda form: {"other_metric": 0.5}
    )
    mean, std = al._surrogate_score({}, "autodeposition_coating", [], "salt_spray_hours")
    assert (mean, std) == (0.0, 0.0)

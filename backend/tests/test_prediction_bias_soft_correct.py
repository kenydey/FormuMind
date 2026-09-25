"""Top-5‴ #2: prediction bias soft-correct unit tests."""
from __future__ import annotations

from app.config import Settings, get_settings
from app.services.prediction_bias_correct import soft_correct_predicted


def test_soft_correct_noop_when_flag_off():
    assert Settings.model_fields["prediction_bias_soft_correct"].default is False
    pred = {"salt_spray_hours": 800.0, "cost_cny_per_kg": 12.0}
    out, metrics = soft_correct_predicted(
        pred,
        by_metric={
            "salt_spray_hours": {"n": 5, "mean_error": 50.0, "rmse": 55.0, "mae": 50.0, "max_abs": 60.0}
        },
    )
    assert metrics == []
    assert out == pred


def test_soft_correct_subtracts_mean_error(monkeypatch):
    monkeypatch.setenv("FORMUMIND_PREDICTION_BIAS_SOFT_CORRECT", "true")
    monkeypatch.setenv("FORMUMIND_PREDICTION_BIAS_SOFT_CORRECT_MIN_N", "3")
    get_settings.cache_clear()
    pred = {"salt_spray_hours": 800.0, "cost_cny_per_kg": 12.0}
    out, metrics = soft_correct_predicted(
        pred,
        by_metric={
            "salt_spray_hours": {
                "n": 5,
                "mean_error": 50.0,
                "rmse": 55.0,
                "mae": 50.0,
                "max_abs": 60.0,
            },
            "cost_cny_per_kg": {"n": 2, "mean_error": 1.0, "rmse": 1.0, "mae": 1.0, "max_abs": 1.0},
        },
    )
    assert "salt_spray_hours" in metrics
    assert out["salt_spray_hours"] == 750.0
    # n=2 < min_n=3 → untouched
    assert "cost_cny_per_kg" not in metrics
    assert out["cost_cny_per_kg"] == 12.0
    get_settings.cache_clear()


def test_soft_correct_skips_insufficient_n(monkeypatch):
    monkeypatch.setenv("FORMUMIND_PREDICTION_BIAS_SOFT_CORRECT", "true")
    get_settings.cache_clear()
    out, metrics = soft_correct_predicted(
        {"salt_spray_hours": 100.0},
        by_metric={"salt_spray_hours": {"n": 1, "mean_error": 10.0}},
    )
    assert metrics == []
    assert out["salt_spray_hours"] == 100.0
    get_settings.cache_clear()

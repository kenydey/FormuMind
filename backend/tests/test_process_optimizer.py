"""Tests for the process parameter optimizer (v0.5)."""
import pytest

from app.domain.schemas import ProcessOptRequest, ProcessOptResult, ProductDomain
from app.services.process_optimizer import (
    PROCESS_LEVERS,
    predict_process_outcome,
    run_process_optimization,
)


def test_process_levers_defined_for_all_domains():
    for domain in ProductDomain:
        levers = PROCESS_LEVERS.get(domain)
        assert levers is not None, f"No process levers for {domain}"
        assert len(levers) >= 2


def test_predict_anticorrosion_contains_expected_keys():
    from app.services.process_optimizer import _predict_anticorrosion

    out = _predict_anticorrosion({
        "cure_temperature_c": 80.0,
        "cure_time_min": 45.0,
        "dispersion_rpm": 1600.0,
        "film_thickness_um": 80.0,
    })
    assert "cure_conversion_pct" in out
    assert "salt_spray_improvement_h" in out
    assert 0 < out["cure_conversion_pct"] <= 100


def test_predict_degreaser_contains_expected_keys():
    from app.services.process_optimizer import _predict_degreaser

    out = _predict_degreaser({
        "bath_temperature_c": 60.0,
        "immersion_time_min": 8.0,
        "ph_setpoint": 12.0,
    })
    assert "cleaning_efficiency_pct" in out
    assert 0 < out["cleaning_efficiency_pct"] <= 100


def test_predict_surface_treatment_contains_expected_keys():
    from app.services.process_optimizer import _predict_surface_treatment

    out = _predict_surface_treatment({
        "treat_temperature_c": 40.0,
        "immersion_time_min": 10.0,
        "accelerator_factor": 1.0,
    })
    assert "coating_weight_gsm" in out
    assert out["coating_weight_gsm"] > 0


def test_run_process_optimization_returns_result():
    req = ProcessOptRequest(domain=ProductDomain.anticorrosion_coating, iterations=8)
    result = run_process_optimization(req)
    assert isinstance(result, ProcessOptResult)
    assert result.domain == "anticorrosion_coating"
    assert result.iterations == 8
    assert len(result.history) == 8
    assert len(result.best_params) >= 2
    assert result.engine in {"numpy-ucb", "optuna-tpe", "summit-sobo", "botorch-ei"}


def test_run_process_optimization_all_domains():
    for domain in ProductDomain:
        req = ProcessOptRequest(domain=domain, iterations=4)
        result = run_process_optimization(req)
        assert result.best_params
        assert result.predicted_outcome


def test_process_optimization_best_params_within_bounds():
    req = ProcessOptRequest(domain=ProductDomain.anticorrosion_coating, iterations=6)
    result = run_process_optimization(req)
    levers = PROCESS_LEVERS[ProductDomain.anticorrosion_coating]
    for lever in levers:
        val = result.best_params.get(lever.name)
        if val is not None:
            assert lever.low <= val <= lever.high + 1e-6, (
                f"{lever.name}={val} outside [{lever.low}, {lever.high}]"
            )

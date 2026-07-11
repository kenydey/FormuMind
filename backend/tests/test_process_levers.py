"""Process levers (cure/bath temperature, immersion time) must reach the predictor.

Regression tests for the review findings where optimization scanned process
dimensions that had no effect on predictions (fixed process dict per run) and
featurization dropped surface-treatment process factors entirely.
"""
from __future__ import annotations

from app.domain import features
from app.domain.levers import process_factors
from app.domain.schemas import ObjectiveSpec, ProductDomain, Requirement, Substrate
from app.pipeline import workflow
from app.services import predictor


def test_featurize_includes_all_process_keys():
    req = Requirement(domain=ProductDomain.surface_treatment, substrate=Substrate.magnesium_alloy)
    from app.domain import knowledge

    form = knowledge.baseline_formulation(req)
    feats = features.featurize(form, {"bath_temperature_c": 50.0, "immersion_time_min": 120.0})
    assert feats["bath_temperature_c"] == 50.0
    assert feats["immersion_time_min"] == 120.0
    assert set(features.PROCESS_KEYS) <= set(feats)


def test_feature_vector_changes_with_process_levers():
    req = Requirement(domain=ProductDomain.surface_treatment, substrate=Substrate.magnesium_alloy)
    from app.domain import knowledge

    form = knowledge.baseline_formulation(req)
    v1 = features.vector(form, {"bath_temperature_c": 25.0})
    v2 = features.vector(form, {"bath_temperature_c": 65.0})
    assert v1 != v2


def test_process_factors_extracts_only_process_levers():
    values = {
        "Hexafluorozirconic acid": 1.5,
        "bath_temperature_c": 45.0,
        "immersion_time_min": 90.0,
        "cure_temperature_c": 120.0,
    }
    out = process_factors(values)
    assert out == {
        "bath_temperature_c": 45.0,
        "immersion_time_min": 90.0,
        "cure_temperature_c": 120.0,
    }


def test_optimization_passes_per_iteration_process_to_predictor(monkeypatch):
    """cure_temperature_c as an optimizer lever must vary in the predictor's process dict."""
    seen: list[dict] = []
    real_predict = predictor.predict

    def spy_predict(form, process=None):
        seen.append(dict(process or {}))
        return real_predict(form, process)

    monkeypatch.setattr(predictor, "predict", spy_predict)

    from app.domain.schemas import LeverSpec

    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        cure_temperature_c=80.0,
        objectives=[ObjectiveSpec(metric="salt_spray_hours", weight=1.0, direction="maximize")],
        # Explicit process lever so the optimizer scans cure temperature.
        levers=[
            LeverSpec(name="Zinc phosphate", low=4.0, high=12.0, unit="wt%"),
            LeverSpec(name="cure_temperature_c", low=60.0, high=140.0, unit="C"),
        ],
    )
    workflow.run_optimization(req, iterations=8, engine="native")

    iter_processes = [p for p in seen if "cure_temperature_c" in p]
    assert iter_processes, "predictor never received cure_temperature_c"
    temps = {p["cure_temperature_c"] for p in iter_processes}
    assert len(temps) > 1, "cure temperature lever is inert — same value in every iteration"

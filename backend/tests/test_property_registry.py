import pytest

from app.domain.examples import load_example
from app.domain.schemas import MetricPriorSpec
from app.pipeline import reconstruct
from app.services.property.registry import predict_all


def test_predict_all_returns_tiers():
    req = load_example("anticorrosion_coating")
    form = reconstruct.formulation_from_factors(
        req.domain,
        {"Zinc phosphate": 8.0, "Bisphenol-A epoxy (DGEBA)": 38.0, "Polyamide hardener": 14.0},
    )
    props, std, tiers = predict_all(form, {"cure_temperature_c": 80.0}, req)
    assert "salt_spray_hours" in props
    assert tiers.get("salt_spray_hours") in ("mechanistic", "trained", "role-based", "prior")
    assert isinstance(std, dict)


def test_metric_prior_yaml_evaluated():
    req = load_example("degreaser")
    req.metric_priors = [
        MetricPriorSpec(
            metric="custom_score",
            prior_yaml="""
prior:
  intercept: 10
  terms:
    - role: surfactant
      coef: 2
""",
            confidence="prior",
        )
    ]
    form = reconstruct.formulation_from_factors(
        req.domain,
        {"Nonionic surfactant (C12-14 EO7)": 5.0, "Sodium metasilicate": 6.0},
    )
    props, _, tiers = predict_all(form, None, req)
    assert props.get("custom_score") == pytest.approx(20.0)
    assert tiers.get("custom_score") == "prior"

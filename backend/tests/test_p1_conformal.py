"""P1 #19: conformal calibration for predict_with_std."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.domain import features
from app.domain.schemas import ExperimentRecord, ProductDomain
from app.pipeline import reconstruct
from app.services.training import ModelRegistry


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_MODEL_ARTIFACTS_DIR", str(tmp_path / "models"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _records(n=16):
    rng_zinc = [2.0 + i * 0.6 for i in range(n)]
    out = []
    for zinc in rng_zinc:
        out.append(
            ExperimentRecord(
                domain=ProductDomain.anticorrosion_coating,
                factors={
                    "Zinc phosphate": zinc,
                    "Bisphenol-A epoxy (DGEBA)": 38.0,
                    "Polyamide hardener": 14.0,
                },
                cure_temperature_c=80.0,
                # Mild noise so residuals are non-zero
                measured={"salt_spray_hours": 200.0 + 80.0 * zinc + (zinc % 1.0) * 5.0},
            )
        )
    return out


def test_model_info_has_conformal_q90(tmp_path):
    reg = ModelRegistry(path=str(tmp_path / "exp.json"))
    reg.add(_records(16))
    info = next(i for i in reg.info() if i.metric == "salt_spray_hours")
    assert info.uncertainty_calibrated is True
    assert info.conformal_q90 is not None
    assert info.conformal_q90 >= 0.0


def test_predict_with_std_at_least_conformal_scale(tmp_path):
    reg = ModelRegistry(path=str(tmp_path / "exp.json"))
    reg.add(_records(16))
    info = next(i for i in reg.info() if i.metric == "salt_spray_hours")
    form = reconstruct.formulation_from_factors(
        ProductDomain.anticorrosion_coating, {"Zinc phosphate": 8.0}
    )
    vec = features.vector(form, {"cure_temperature_c": 80.0})
    pred, std, n = reg.predict_with_std(
        ProductDomain.anticorrosion_coating, "salt_spray_hours", vec
    )
    assert n == 16
    assert pred > 0
    # Calibrated std must be at least q90/1.645
    floor = float(info.conformal_q90) / 1.64485362695
    assert std + 1e-9 >= floor


def test_predict_interval_uses_q90(tmp_path):
    reg = ModelRegistry(path=str(tmp_path / "exp.json"))
    reg.add(_records(16))
    info = next(i for i in reg.info() if i.metric == "salt_spray_hours")
    form = reconstruct.formulation_from_factors(
        ProductDomain.anticorrosion_coating, {"Zinc phosphate": 8.0}
    )
    vec = features.vector(form, {"cure_temperature_c": 80.0})
    pred, lo, hi, n = reg.predict_interval(
        ProductDomain.anticorrosion_coating, "salt_spray_hours", vec
    )
    assert n == 16
    assert lo <= pred <= hi
    assert abs((hi - lo) / 2 - float(info.conformal_q90)) < 1e-5

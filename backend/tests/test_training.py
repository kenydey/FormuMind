import numpy as np
import pytest

from app.domain import features
from app.domain.schemas import ExperimentRecord, ProductDomain
from app.pipeline import reconstruct
from app.services import predictor
from app.services.training import ModelRegistry, registry


def _coating_records(n=10, slope=80.0, intercept=200.0, seed=0):
    """Synthetic DOE results: salt spray is linear in zinc-phosphate loading."""
    rng = np.random.default_rng(seed)
    records = []
    for _ in range(n):
        zinc = float(rng.uniform(2.0, 14.0))
        records.append(
            ExperimentRecord(
                domain=ProductDomain.anticorrosion_coating,
                factors={"Zinc phosphate": zinc, "Bisphenol-A epoxy (DGEBA)": 38.0, "Polyamide hardener": 14.0},
                cure_temperature_c=80.0,
                measured={"salt_spray_hours": intercept + slope * zinc},
            )
        )
    return records


def test_registry_trains_and_learns_linear_relation(tmp_path):
    reg = ModelRegistry(path=str(tmp_path / "exp.json"))
    reg.add(_coating_records(n=12))
    infos = reg.info()
    assert infos, "a model should be trained"
    salt = next(i for i in infos if i.metric == "salt_spray_hours")
    assert salt.n_samples == 12
    assert salt.r2 > 0.9  # captures the linear signal

    # Predict at a held-out point and compare to ground truth (200 + 80*zinc).
    form = reconstruct.formulation_from_factors(ProductDomain.anticorrosion_coating, {"Zinc phosphate": 10.0})
    vec = features.vector(form, {"cure_temperature_c": 80.0})
    pred, n = reg.predict(ProductDomain.anticorrosion_coating, "salt_spray_hours", vec)
    assert n == 12
    assert pred == pytest.approx(200.0 + 80.0 * 10.0, rel=0.15)


def test_below_min_samples_no_model(tmp_path):
    reg = ModelRegistry(path=str(tmp_path / "exp.json"))
    reg.add(_coating_records(n=2))
    assert reg.info() == []  # under the min-samples threshold


def test_persistence_round_trip(tmp_path):
    path = str(tmp_path / "exp.json")
    reg1 = ModelRegistry(path=path)
    reg1.add(_coating_records(n=8))
    assert reg1.total_records == 8

    reg2 = ModelRegistry(path=path)  # reload from disk
    assert reg2.total_records == 8
    assert reg2.info(), "models rebuilt from persisted dataset on load"


def test_predictor_blends_trained_model():
    """Feeding back high measured values must pull predictions above the prior."""
    registry.reset(persist=True)
    try:
        form = reconstruct.formulation_from_factors(ProductDomain.anticorrosion_coating, {"Zinc phosphate": 8.0})
        baseline = predictor.predict(form, {"cure_temperature_c": 80.0})["salt_spray_hours"]

        # Measured results far above the empirical prior.
        records = _coating_records(n=10, slope=0.0, intercept=4000.0)
        registry.add(records)
        blended = predictor.predict(form, {"cure_temperature_c": 80.0})["salt_spray_hours"]

        assert blended > baseline  # model evidence moved the prediction up
        assert blended < 4000.0    # but blended, not fully overridden, at n=10
    finally:
        registry.reset(persist=True)

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


def test_predict_with_std_returns_three_tuple(tmp_path):
    reg = ModelRegistry(path=str(tmp_path / "exp.json"))
    reg.add(_coating_records(n=12))
    form = reconstruct.formulation_from_factors(ProductDomain.anticorrosion_coating, {"Zinc phosphate": 8.0})
    vec = features.vector(form, {"cure_temperature_c": 80.0})
    result = reg.predict_with_std(ProductDomain.anticorrosion_coating, "salt_spray_hours", vec)
    assert result is not None
    pred, std, n = result
    assert n == 12
    assert pred > 0
    assert std >= 0.0  # non-negative uncertainty


def test_predict_with_std_returns_none_for_unknown(tmp_path):
    reg = ModelRegistry(path=str(tmp_path / "exp.json"))
    assert reg.predict_with_std(ProductDomain.anticorrosion_coating, "salt_spray_hours", [0.0] * 16) is None


def test_scoped_project_dataset_excludes_empty_project_id(tmp_path):
    """Empty project_id rows must not pollute a named project's training set."""
    reg = ModelRegistry(path=str(tmp_path / "exp.json"))
    domain = ProductDomain.anticorrosion_coating
    empty_rows = [
        ExperimentRecord(
            domain=domain,
            project_id="",
            factors={"Zinc phosphate": 4.0, "Bisphenol-A epoxy (DGEBA)": 38.0, "Polyamide hardener": 14.0},
            cure_temperature_c=80.0,
            measured={"salt_spray_hours": 400.0},
        )
        for _ in range(6)
    ]
    proj_rows = [
        ExperimentRecord(
            domain=domain,
            project_id="proj-a",
            factors={"Zinc phosphate": 8.0, "Bisphenol-A epoxy (DGEBA)": 38.0, "Polyamide hardener": 14.0},
            cure_temperature_c=80.0,
            measured={"salt_spray_hours": 800.0},
        )
        for _ in range(6)
    ]
    reg.add(empty_rows + proj_rows, retrain=True)
    data = reg._dataset(domain, "salt_spray_hours", project_id="proj-a")
    assert data is not None
    X, y = data
    assert len(y) == 6
    assert float(np.mean(y)) == pytest.approx(800.0)


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


def test_predictor_predict_full_returns_std():
    """predict_full should return non-empty predicted_std when trained models exist."""
    registry.reset(persist=True)
    try:
        records = _coating_records(n=10, slope=80.0, intercept=200.0)
        registry.add(records)
        form = reconstruct.formulation_from_factors(ProductDomain.anticorrosion_coating, {"Zinc phosphate": 8.0})
        props, std = predictor.predict_full(form, {"cure_temperature_c": 80.0})
        assert "salt_spray_hours" in props
        assert "cost_cny_per_kg" in props
        assert "voc_gpl" in props
        assert "sustainability_idx" in props
        assert "salt_spray_hours" in std
        assert std["salt_spray_hours"] >= 0.0
    finally:
        registry.reset(persist=True)

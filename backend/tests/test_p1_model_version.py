"""P1 #20: ModelInfo version fields + disk artifact reload/rollback."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.domain.schemas import ExperimentRecord, ProductDomain
from app.services.training import ModelRegistry


@pytest.fixture
def artifacts_dir(tmp_path, monkeypatch):
    root = tmp_path / "models"
    monkeypatch.setenv("FORMUMIND_MODEL_ARTIFACTS_DIR", str(root))
    monkeypatch.setenv("FORMUMIND_MODEL_PERSIST_ENABLED", "true")
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


def _records(n=10, zinc_base=4.0):
    out = []
    for i in range(n):
        zinc = zinc_base + i * 0.5
        out.append(
            ExperimentRecord(
                domain=ProductDomain.anticorrosion_coating,
                factors={
                    "Zinc phosphate": zinc,
                    "Bisphenol-A epoxy (DGEBA)": 38.0,
                    "Polyamide hardener": 14.0,
                },
                cure_temperature_c=80.0,
                measured={"salt_spray_hours": 200.0 + 80.0 * zinc},
            )
        )
    return out


def test_train_writes_version_metadata(artifacts_dir, tmp_path):
    reg = ModelRegistry(path=str(tmp_path / "exp.json"))
    reg.add(_records(12))
    infos = reg.info()
    assert infos
    salt = next(i for i in infos if i.metric == "salt_spray_hours")
    assert salt.trained_at
    assert salt.data_hash
    assert salt.feature_version
    assert salt.version_id
    # Artifact on disk
    versions = reg.list_model_versions(salt.project_id, salt.metric)
    assert versions
    assert versions[0]["is_current"] is True


def test_reload_uses_cached_artifact_without_retrain(artifacts_dir, tmp_path, monkeypatch):
    path = str(tmp_path / "exp.json")
    reg1 = ModelRegistry(path=path)
    reg1.add(_records(12))
    info1 = next(i for i in reg1.info() if i.metric == "salt_spray_hours")
    vid1 = info1.version_id

    # Second registry: should load from disk (same data_hash).
    fit_calls = {"n": 0}
    real_make = __import__("app.services.training", fromlist=["_make_regressor"])._make_regressor

    def counting_make():
        fit_calls["n"] += 1
        model, backend = real_make()
        orig_fit = model.fit

        def wrapped(X, y):
            fit_calls["n"] += 10  # mark real fit
            return orig_fit(X, y)

        model.fit = wrapped
        return model, backend

    monkeypatch.setattr("app.services.training._make_regressor", counting_make)
    reg2 = ModelRegistry(path=path)
    info2 = next(i for i in reg2.info() if i.metric == "salt_spray_hours")
    assert info2.version_id == vid1
    assert info2.data_hash == info1.data_hash
    # _make_regressor may be called for kfold elsewhere only on train; cache path
    # should not call fit (+10). Allow probe calls without fit.
    assert fit_calls["n"] < 10


def test_rollback_switches_current(artifacts_dir, tmp_path):
    path = str(tmp_path / "exp.json")
    reg = ModelRegistry(path=path)
    reg.add(_records(12, zinc_base=2.0))
    first = next(i for i in reg.info() if i.metric == "salt_spray_hours")
    v1 = first.version_id
    # Retrain with different data → new version
    reg.add(_records(12, zinc_base=8.0))
    second = next(i for i in reg.info() if i.metric == "salt_spray_hours")
    v2 = second.version_id
    assert v1 != v2
    rolled = reg.rollback_model(first.project_id, "salt_spray_hours", v1)
    assert rolled is not None
    assert rolled.version_id == v1
    current = next(i for i in reg.info() if i.metric == "salt_spray_hours")
    assert current.version_id == v1

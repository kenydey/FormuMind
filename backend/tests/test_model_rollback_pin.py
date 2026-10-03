"""A rollback must stick.

``rollback_model`` loaded an old artifact into memory and pointed ``current.json`` at it,
but nothing remembered the choice: the next experiment submit (``auto_retrain`` on),
``POST /api/train``, or a restart whose data no longer matched the old artifact's hash
retrained and overwrote it. A rollback now pins the version; retrains archive new versions
beside it, and an explicit release switches to the newest one.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.domain.schemas import ExperimentRecord, ProductDomain
from app.services import model_store
from app.services.training import ModelRegistry

METRIC = "salt_spray_hours"


@pytest.fixture
def artifacts_dir(tmp_path, monkeypatch):
    root = tmp_path / "models"
    monkeypatch.setenv("FORMUMIND_MODEL_ARTIFACTS_DIR", str(root))
    monkeypatch.setenv("FORMUMIND_MODEL_PERSIST_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_AUTO_RETRAIN", "true")
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


def _records(n=12, zinc_base=4.0):
    return [
        ExperimentRecord(
            domain=ProductDomain.anticorrosion_coating,
            factors={
                "Zinc phosphate": zinc_base + i * 0.5,
                "Bisphenol-A epoxy (DGEBA)": 38.0,
                "Polyamide hardener": 14.0,
            },
            cure_temperature_c=80.0,
            measured={METRIC: 200.0 + 80.0 * (zinc_base + i * 0.5)},
        )
        for i in range(n)
    ]


def _salt(reg):
    return next(i for i in reg.info() if i.metric == METRIC)


@pytest.fixture
def two_versions(artifacts_dir, tmp_path):
    """A registry with an older version v1 and a newer, current v2."""
    path = str(tmp_path / "exp.json")
    reg = ModelRegistry(path=path)
    reg.add(_records(12, zinc_base=2.0))
    v1 = _salt(reg).version_id
    reg.add(_records(12, zinc_base=8.0))
    v2 = _salt(reg).version_id
    assert v1 != v2
    return reg, path, _salt(reg).project_id, v1, v2


def test_rollback_pins_the_chosen_version_across_retrains(two_versions):
    reg, _path, pid, v1, v2 = two_versions

    rolled = reg.rollback_model(pid, METRIC, v1)
    assert rolled is not None and rolled.version_id == v1
    assert rolled.pinned is True
    assert rolled.newer_version_id == v2

    # A new experiment batch retrains — and must not take over from the pinned version.
    reg.add(_records(12, zinc_base=14.0))
    served = _salt(reg)
    assert served.version_id == v1
    assert served.pinned is True
    assert served.newer_version_id and served.newer_version_id not in (v1, v2)

    rows = {v["version_id"]: v for v in reg.list_model_versions(pid, METRIC)}
    assert rows[v1]["is_current"] is True and rows[v1]["pinned"] is True
    assert rows[served.newer_version_id]["is_current"] is False  # archived, waiting

    # An explicit retrain does not unpin either.
    reg.train()
    assert _salt(reg).version_id == v1


def test_the_pin_survives_a_restart(two_versions):
    reg, path, pid, v1, _v2 = two_versions
    reg.rollback_model(pid, METRIC, v1)

    restarted = ModelRegistry(path=path)  # reloads records, retrains, finds the pin
    served = _salt(restarted)
    assert served.version_id == v1
    assert served.pinned is True


def test_rolling_back_to_the_newest_version_pins_nothing(two_versions):
    reg, _path, pid, _v1, v2 = two_versions
    rolled = reg.rollback_model(pid, METRIC, v2)
    assert rolled is not None
    assert rolled.pinned is False and rolled.newer_version_id is None
    assert model_store.pinned_version_id(pid, METRIC) is None


def test_unpin_serves_the_newest_archived_version_and_retraining_resumes(two_versions):
    reg, _path, pid, v1, v2 = two_versions
    reg.rollback_model(pid, METRIC, v1)
    reg.add(_records(12, zinc_base=14.0))
    newest = _salt(reg).newer_version_id
    assert newest and newest != v1

    released = reg.unpin_model(pid, METRIC)
    assert released is not None
    assert released.version_id == newest
    assert released.pinned is False and released.newer_version_id is None
    assert _salt(reg).version_id == newest

    # With the pin gone, the next batch retrains and takes over as before.
    reg.add(_records(12, zinc_base=20.0))
    after = _salt(reg)
    assert after.version_id not in (v1, v2, newest)
    assert after.pinned is False


def test_unpin_with_nothing_archived_is_none(artifacts_dir, tmp_path):
    reg = ModelRegistry(path=str(tmp_path / "exp.json"))
    assert reg.unpin_model("nope", METRIC) is None


def test_a_pin_whose_artifact_is_gone_is_released_instead_of_serving_nothing(two_versions):
    reg, _path, pid, v1, _v2 = two_versions
    reg.rollback_model(pid, METRIC, v1)
    next(p for p in model_store.metric_dir(pid, METRIC).glob(f"{v1}.joblib")).unlink()

    reg.train()
    served = _salt(reg)  # trained normally, no crash, no ghost pin
    assert served.version_id != v1
    assert served.pinned is False
    assert model_store.pinned_version_id(pid, METRIC) is None


def test_the_version_list_carries_what_a_picker_needs_without_loading_models(two_versions):
    reg, _path, pid, v1, v2 = two_versions
    rows = {v["version_id"]: v for v in reg.list_model_versions(pid, METRIC)}
    assert rows[v1]["n_samples"] == 12  # first batch
    assert rows[v2]["n_samples"] == 24  # both batches
    for vid in (v1, v2):
        row = rows[vid]
        assert row["trained_at"]
        assert row["backend"]
        assert row["r2"] is not None


def test_candidates_are_not_archived_twice_for_the_same_data(two_versions):
    reg, _path, pid, v1, _v2 = two_versions
    reg.rollback_model(pid, METRIC, v1)
    reg.add(_records(12, zinc_base=14.0))
    count = len(reg.list_model_versions(pid, METRIC))
    reg.train()
    reg.train()
    assert len(reg.list_model_versions(pid, METRIC)) == count


# ── HTTP ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def api(two_versions, monkeypatch):
    reg, _path, pid, v1, v2 = two_versions
    import app.api.experiments as exp_api
    from app.main import app
    from app.services import training

    monkeypatch.setattr(training, "registry", reg)
    monkeypatch.setattr(exp_api, "registry", reg)
    return TestClient(app), reg, pid, v1, v2


def test_rollback_endpoint_reports_the_pin_and_unpin_releases_it(api):
    client, reg, pid, v1, v2 = api

    res = client.post("/api/models/rollback", json={"project_id": pid, "metric": METRIC, "version_id": v1})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["version_id"] == v1 and body["pinned"] is True and body["newer_version_id"] == v2

    versions = client.get("/api/models/versions", params={"project_id": pid, "metric": METRIC}).json()
    assert {v["version_id"] for v in versions} >= {v1, v2}
    assert next(v for v in versions if v["version_id"] == v1)["pinned"] is True

    res = client.post("/api/models/unpin", json={"project_id": pid, "metric": METRIC})
    assert res.status_code == 200, res.text
    assert res.json()["version_id"] == v2 and res.json()["pinned"] is False


def test_unpin_endpoint_404_when_there_is_nothing_to_serve(api):
    client, *_ = api
    res = client.post("/api/models/unpin", json={"project_id": "ghost", "metric": "nothing"})
    assert res.status_code == 404


def test_responses_say_when_a_pinned_model_kept_serving(api):
    client, reg, pid, v1, _v2 = api
    client.post("/api/models/rollback", json={"project_id": pid, "metric": METRIC, "version_id": v1})

    body = {"records": [r.model_dump(mode="json") for r in _records(12, zinc_base=14.0)], "retrain": True}
    res = client.post("/api/experiments", json=body)
    assert res.status_code == 200, res.text
    assert "pinned to a rolled-back version" in res.json()["message"]
    assert METRIC in res.json()["message"]

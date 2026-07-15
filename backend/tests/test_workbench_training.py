"""Tests for workbench → ModelRegistry auto-ingest (Sprint 1)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.campaign_store import SqliteCampaignStore, reset_campaign_store
from app.db.database import make_engine, make_session_factory
from app.db.store import JsonExperimentStore
from app.domain.schemas import DOEPlan, DOERun, ProductDomain, Requirement, ObjectiveSpec
from app.main import app
from app.services.training import ModelRegistry, registry
from app.services import workbench_training


def _plan() -> DOEPlan:
    return DOEPlan(
        design="lhs",
        factors=[],
        runs=[
            DOERun(run_id=1, coded={}, natural={"Zinc phosphate": 8.0, "cure_temperature_c": 80.0}),
        ],
        notes="test",
        plan_id="abc12345",
        domain=ProductDomain.anticorrosion_coating,
    )


@pytest.fixture(autouse=True)
def _sqlite_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUMIND_CAMPAIGN_BACKEND", "sqlite")
    monkeypatch.setenv("FORMUMIND_WORKBENCH_AUTO_TRAIN", "true")
    get_settings.cache_clear()
    db_path = tmp_path / "wb_train.db"
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    reset_campaign_store(SqliteCampaignStore(factory))
    store = JsonExperimentStore(str(tmp_path / "exp.json"))
    registry._store = store  # noqa: SLF001
    registry.load()
    yield
    reset_campaign_store(None)
    get_settings.cache_clear()


def test_row_to_experiment_record_completed():
    from app.db.campaign_types import WorkbenchRow

    row = WorkbenchRow(
        id=1,
        campaign_id=1,
        item_id="local_c1_r1",
        status="Completed",
        planned_params={"Zinc phosphate": 8.0},
        actual_params={"Zinc phosphate": 8.5, "cure_temperature_c": 81.0},
        measurements={"salt_spray_hours": 900.0},
    )
    rec = workbench_training.row_to_experiment_record(
        row, campaign_id=1, domain=ProductDomain.anticorrosion_coating
    )
    assert rec is not None
    assert rec.measured["salt_spray_hours"] == 900.0
    assert rec.label == "wb:1:local_c1_r1"


def test_sync_workbench_ingests_training(tmp_path):
    client = TestClient(app)
    created = client.post("/api/experiments/workbench/campaigns", json={"plan": _plan().model_dump()}).json()
    campaign_id = created["campaign_id"]
    row = created["rows"][0]
    before = registry.total_records

    sync = client.put(
        "/api/experiments/workbench/sync",
        json={
            "campaign_id": campaign_id,
            "rows": [
                {
                    "id": row["id"],
                    "status": "Pending",
                    "actual_params": {"Zinc phosphate": 8.5, "cure_temperature_c": 81.0},
                    "measurements": {"salt_spray_hours": 860.0},
                }
            ],
        },
    )
    assert sync.status_code == 200
    body = sync.json()
    assert body["training_ingested"] == 1
    assert registry.total_records == before + 1

    # Idempotent second sync
    sync2 = client.put(
        "/api/experiments/workbench/sync",
        json={
            "campaign_id": campaign_id,
            "rows": [body["rows"][0]],
        },
    )
    assert sync2.json()["training_ingested"] == 0

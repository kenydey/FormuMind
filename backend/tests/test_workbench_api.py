"""Tests for DOE workbench campaign persistence and sync API."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.campaign_store import CampaignStore
from app.db.database import make_engine, make_session_factory
from app.db import campaign_store as campaign_store_module
from app.domain.schemas import DOEPlan, DOERun, ProductDomain
from app.main import app


def _plan() -> DOEPlan:
    return DOEPlan(
        design="lhs",
        factors=[],
        runs=[
            DOERun(run_id=1, coded={}, natural={"Zinc phosphate": 8.0, "cure_temperature_c": 80.0}),
            DOERun(run_id=2, coded={}, natural={"Zinc phosphate": 10.0, "cure_temperature_c": 85.0}),
        ],
        notes="test",
        plan_id="abc12345",
        domain=ProductDomain.anticorrosion_coating,
    )


def _client_with_memory_db(tmp_path):
    db_path = tmp_path / "workbench.db"
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    campaign_store_module._store = CampaignStore(factory)
    return TestClient(app)


def test_create_workbench_campaign(tmp_path):
    client = _client_with_memory_db(tmp_path)
    res = client.post("/api/experiments/workbench/campaigns", json={"plan": _plan().model_dump()})
    assert res.status_code == 200
    body = res.json()
    assert body["campaign_id"] >= 1
    assert len(body["rows"]) == 2
    assert body["rows"][0]["status"] == "Pending"
    assert body["rows"][0]["planned_params"]["Zinc phosphate"] == 8.0


def test_sync_workbench_marks_completed(tmp_path):
    client = _client_with_memory_db(tmp_path)
    created = client.post("/api/experiments/workbench/campaigns", json={"plan": _plan().model_dump()}).json()
    campaign_id = created["campaign_id"]
    row = created["rows"][0]

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
    updated = sync.json()
    assert updated["updated"] == 1
    assert updated["rows"][0]["status"] == "Completed"
    assert updated["rows"][0]["measurements"]["salt_spray_hours"] == 860.0


def test_fetch_campaign_data_for_baybe():
    import pandas as pd

    from app.db.models import ExperimentRecord as WorkbenchRow
    from app.services.engines.baybe_engine import fetch_campaign_data_for_baybe

    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    store = CampaignStore(factory)
    campaign = store.create_from_plan(_plan())

    with factory() as session:
        row = session.query(WorkbenchRow).filter(WorkbenchRow.campaign_id == campaign.id).first()
        row.actual_params = {"Zinc phosphate": 9.0, "cure_temperature_c": 82.0}
        row.measurements = {"salt_spray_hours": 900.0}
        row.status = "Completed"
        session.commit()

        actual_X, measurements_Y = fetch_campaign_data_for_baybe(campaign.id, session)
        assert not actual_X.empty
        assert not measurements_Y.empty
        assert float(actual_X.iloc[0]["Zinc phosphate"]) == 9.0
        assert float(measurements_Y.iloc[0]["salt_spray_hours"]) == 900.0
        merged = pd.concat([actual_X, measurements_Y], axis=1)
        assert "salt_spray_hours" in merged.columns

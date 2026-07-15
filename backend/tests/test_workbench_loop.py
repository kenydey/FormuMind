"""Tests for workbench sync → closed-loop dispatch (Phase 3B)."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.campaign_store import SqliteCampaignStore, reset_campaign_store
from app.db.database import make_engine, make_session_factory
from app.db.store import JsonExperimentStore
from app.domain.schemas import DOEPlan, DOERun, ProductDomain, Requirement
from app.main import app
from app.services import workbench_loop
from app.services.training import registry


def _plan() -> DOEPlan:
    return DOEPlan(
        design="lhs",
        factors=[],
        runs=[
            DOERun(run_id=1, coded={}, natural={"Zinc phosphate": 8.0, "cure_temperature_c": 80.0}),
        ],
        notes="test",
        plan_id="loop1234",
        domain=ProductDomain.anticorrosion_coating,
    )


@pytest.fixture(autouse=True)
def _sqlite_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUMIND_CAMPAIGN_BACKEND", "sqlite")
    monkeypatch.setenv("FORMUMIND_WORKBENCH_AUTO_TRAIN", "true")
    monkeypatch.setenv("FORMUMIND_AUTO_LOOP_ON_SYNC", "false")
    get_settings.cache_clear()
    db_path = tmp_path / "wb_loop.db"
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    reset_campaign_store(SqliteCampaignStore(factory))
    store = JsonExperimentStore(str(tmp_path / "exp.json"))
    registry._store = store  # noqa: SLF001
    registry.load()
    yield
    reset_campaign_store(None)
    get_settings.cache_clear()


def test_should_trigger_loop_respects_flags():
    assert not workbench_loop.should_trigger_loop_after_sync(0, trigger_loop=True)
    assert not workbench_loop.should_trigger_loop_after_sync(1, trigger_loop=False)
    assert workbench_loop.should_trigger_loop_after_sync(1, trigger_loop=True)
    assert not workbench_loop.should_trigger_loop_after_sync(1, trigger_loop=None)


def test_sync_with_trigger_loop_returns_task_id(tmp_path):
    client = TestClient(app)
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
                    "status": "Completed",
                    "actual_params": {"Zinc phosphate": 8.5, "cure_temperature_c": 81.0},
                    "measurements": {"salt_spray_hours": 860.0},
                }
            ],
            "trigger_loop": True,
        },
    )
    assert sync.status_code == 200
    body = sync.json()
    assert body["training_ingested"] == 1
    assert body["loop_task_id"]
    assert "闭环" in body["loop_message"]


def test_sync_without_trigger_loop_skips_loop():
    client = TestClient(app)
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
                    "status": "Completed",
                    "actual_params": {"Zinc phosphate": 8.5},
                    "measurements": {"salt_spray_hours": 860.0},
                }
            ],
        },
    )
    body = sync.json()
    assert body["training_ingested"] == 1
    assert body.get("loop_task_id") in (None, "")


def test_auto_loop_on_sync_env(monkeypatch):
    monkeypatch.setenv("FORMUMIND_AUTO_LOOP_ON_SYNC", "true")
    get_settings.cache_clear()
    assert workbench_loop.should_trigger_loop_after_sync(2, trigger_loop=None)

    client = TestClient(app)
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
                    "status": "Completed",
                    "actual_params": {"Zinc phosphate": 8.5},
                    "measurements": {"salt_spray_hours": 870.0},
                }
            ],
        },
    )
    assert sync.json()["loop_task_id"]

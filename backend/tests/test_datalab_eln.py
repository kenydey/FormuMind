"""Enterprise ELN (Plan D) — Datalab hard-fail and health reporting."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.campaign_store import DatalabUnavailableError, get_campaign_store, reset_campaign_store
from app.db.datalab_client import check_datalab_reachable
from app.domain.schemas import DOEPlan, DOERun, ProductDomain
from app.main import app


def _plan() -> DOEPlan:
    return DOEPlan(
        design="lhs",
        factors=[],
        runs=[DOERun(run_id=1, coded={}, natural={"Zinc phosphate": 8.0})],
        notes="test",
        plan_id="eln01",
        domain=ProductDomain.anticorrosion_coating,
    )


@pytest.fixture(autouse=True)
def _reset_stores():
    reset_campaign_store(None)
    get_settings.cache_clear()
    yield
    reset_campaign_store(None)
    get_settings.cache_clear()


def test_check_datalab_unreachable_on_bad_url():
    ok, reason = check_datalab_reachable("http://127.0.0.1:1", timeout=0.5)
    assert ok is False
    assert reason


def test_campaign_store_datalab_mode_raises_when_unreachable(monkeypatch):
    monkeypatch.setenv("FORMUMIND_CAMPAIGN_BACKEND", "datalab")
    monkeypatch.setenv("FORMUMIND_DATALAB_API_URL", "http://127.0.0.1:1")
    get_settings.cache_clear()
    with pytest.raises(DatalabUnavailableError):
        get_campaign_store()


def test_workbench_campaign_returns_503_when_datalab_unreachable(monkeypatch):
    monkeypatch.setenv("FORMUMIND_CAMPAIGN_BACKEND", "datalab")
    monkeypatch.setenv("FORMUMIND_DATALAB_API_URL", "http://127.0.0.1:1")
    get_settings.cache_clear()
    client = TestClient(app)
    res = client.post(
        "/api/experiments/workbench/campaigns",
        json={"plan": _plan().model_dump(), "strategy": "DOE-lhs"},
    )
    assert res.status_code == 503
    body = res.json()
    assert "Datalab ELN 不可达" in body["detail"]


def test_health_reports_datalab_degraded_when_required(monkeypatch):
    monkeypatch.setenv("FORMUMIND_CAMPAIGN_BACKEND", "datalab")
    monkeypatch.setenv("FORMUMIND_DATALAB_API_URL", "http://127.0.0.1:1")
    get_settings.cache_clear()
    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "degraded"
    assert body["datalab"]["required"] is True
    assert body["datalab"]["reachable"] is False
    assert body["database"]["scheme"] in ("sqlite", "postgresql")


def test_sqlite_campaign_still_works_in_dev(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUMIND_CAMPAIGN_BACKEND", "sqlite")
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{tmp_path / 'dev.db'}")
    from app.db import database as db_mod
    from app.db.campaign_store import SqliteCampaignStore
    from app.db.database import make_engine, make_session_factory

    db_mod._default.clear()
    factory = make_session_factory(make_engine(f"sqlite:///{tmp_path / 'dev.db'}"))
    reset_campaign_store(SqliteCampaignStore(factory))
    client = TestClient(app)
    res = client.post(
        "/api/experiments/workbench/campaigns",
        json={"plan": _plan().model_dump(), "strategy": "DOE-lhs"},
    )
    assert res.status_code == 200

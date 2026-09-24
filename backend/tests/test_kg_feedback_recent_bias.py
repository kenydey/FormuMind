"""recent_bias extraction tolerates prediction_bias history entries."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.campaign_store import SqliteCampaignStore, reset_campaign_store
from app.db.database import Base, make_engine, make_session_factory
from app.db.entity_store import EntityStore
from app.main import app


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    reset_campaign_store(None)


def test_feedback_report_recent_bias_from_prediction_bias(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path}/bias_report.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    ent = EntityStore(factory)
    import app.db.entity_store as es_mod

    monkeypatch.setattr(es_mod, "_store", ent)
    store = SqliteCampaignStore(factory)
    reset_campaign_store(store)

    from app.db.models import Campaign

    with factory() as session:
        camp = Campaign(name="bias-camp", strategy="t", status="IN_PROGRESS", loop_history=[])
        session.add(camp)
        session.commit()
        cid = camp.id

    store.append_loop_history_sync(
        cid,
        {
            "type": "prediction_bias",
            "at": "2026-09-24T00:00:00Z",
            "n_rows": 2,
            "by_metric": {
                "salt_spray_hours": {
                    "n": 2,
                    "mean_error": 10.0,
                    "rmse": 12.5,
                    "mae": 9.0,
                    "max_abs": 15.0,
                }
            },
        },
    )

    client = TestClient(app)
    r = client.get("/api/kg/feedback/report")
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body.get("recent_bias"), list)
    assert any(
        row.get("campaign_id") == cid and row.get("rmse_by_metric", {}).get("salt_spray_hours") == 12.5
        for row in body["recent_bias"]
    )

"""Regression: legacy campaigns table missing sample_refs must not 500."""
from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from app.db.campaign_store import SqliteCampaignStore, reset_campaign_store
from app.db.database import make_engine, make_session_factory
from app.domain.schemas import DOEPlan, DOERun, ProductDomain
from app.main import app
from fastapi.testclient import TestClient


def test_legacy_campaigns_table_gets_sample_refs_column(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_CAMPAIGN_BACKEND", "sqlite")
    from app.config import get_settings

    get_settings.cache_clear()

    db_path = tmp_path / "legacy.db"
    url = f"sqlite:///{db_path}"
    legacy_engine = create_engine(url)
    with legacy_engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE campaigns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(255) NOT NULL,
                    strategy VARCHAR(64),
                    status VARCHAR(32),
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )

    monkeypatch.setenv("FORMUMIND_DB_URL", url)
    from app.db import database as db_mod

    db_mod._default.clear()
    make_engine(url)
    cols = {c["name"] for c in inspect(legacy_engine).get_columns("campaigns")}
    assert "sample_refs" in cols

    factory = make_session_factory(legacy_engine)
    reset_campaign_store(SqliteCampaignStore(factory))
    client = TestClient(app)
    plan = DOEPlan(
        design="lhs",
        factors=[],
        runs=[DOERun(run_id=1, coded={}, natural={"Zinc phosphate": 8.0})],
        notes="test",
        plan_id="legacy01",
        domain=ProductDomain.anticorrosion_coating,
    )
    res = client.post(
        "/api/experiments/workbench/campaigns",
        json={"plan": plan.model_dump(), "strategy": "DOE-lhs"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["campaign_id"] >= 1
    assert len(body["rows"]) == 1
    reset_campaign_store(None)
    get_settings.cache_clear()


def test_doe_then_workbench_campaign_e2e(tmp_path, monkeypatch):
    """DOE plan generation followed by workbench campaign creation (sqlite backend)."""
    monkeypatch.setenv("FORMUMIND_CAMPAIGN_BACKEND", "sqlite")
    url = f"sqlite:///{tmp_path / 'doe.db'}"
    monkeypatch.setenv("FORMUMIND_DB_URL", url)
    from app.config import get_settings
    from app.db import database as db_mod

    get_settings.cache_clear()
    db_mod._default.clear()
    reset_campaign_store(None)

    req = {
        "domain": "anticorrosion_coating",
        "substrate": "carbon_steel",
        "salt_spray_hours": 500,
        "film_weight_gsm": 70,
        "cure_temperature_c": 80,
        "cleaning_efficiency": 90,
        "voc_limit_gpl": 420,
        "ph_target": None,
        "notes": "",
        "objectives": [],
        "levers": [
            {"name": "Zinc phosphate", "low": 2, "high": 14, "unit": "wt%"},
            {"name": "cure_temperature_c", "low": 50, "high": 80, "unit": "C"},
        ],
    }
    client = TestClient(app)
    doe = client.post("/api/doe?design=lhs", json=req)
    assert doe.status_code == 200, doe.text
    plan = doe.json()
    wb = client.post(
        "/api/experiments/workbench/campaigns",
        json={"plan": plan, "strategy": "DOE-lhs", "requirement": req},
    )
    assert wb.status_code == 200, wb.text
    assert len(wb.json()["rows"]) == len(plan["runs"])
    reset_campaign_store(None)
    get_settings.cache_clear()


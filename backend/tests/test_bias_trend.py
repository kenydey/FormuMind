"""下一轮 1 预测偏差趋势：bias-trend 聚合与阈值告警."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.campaign_store import SqliteCampaignStore, reset_campaign_store
from app.db.database import Base, make_engine, make_session_factory
from app.domain.schemas import DOEPlan, DOERun, ProductDomain, Requirement, ObjectiveSpec
from app.main import app


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_CAMPAIGN_BACKEND", "sqlite")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    reset_campaign_store(None)


def test_bias_trend_returns_entries_and_alert(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path}/bias.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    reset_campaign_store(SqliteCampaignStore(factory))
    client = TestClient(app)
    plan = DOEPlan(design="lhs", factors=[], runs=[DOERun(run_id=1, coded={}, natural={"Zinc phosphate": 9.0, "cure_temperature_c": 82.0})], notes="t", plan_id="b1", domain=ProductDomain.anticorrosion_coating)
    req = Requirement(domain=ProductDomain.anticorrosion_coating, objectives=[ObjectiveSpec(metric="salt_spray_hours", weight=1.0, direction="maximize")])
    r = client.post("/api/experiments/workbench/campaigns", json={"plan": plan.model_dump(), "requirement": req.model_dump()})
    assert r.status_code == 200
    cid = r.json()["campaign_id"]
    # 直接写入 loop_history（含 prediction_bias）
    from app.db.campaign_store import get_campaign_store
    import asyncio

    async def _seed():
        store = get_campaign_store()
        camp = await store.get_campaign(cid)
        # 手动追加两条 bias
        camp.loop_history = [
            {"type": "prediction_bias", "at": "2026-08-28T00:00:00Z", "bias": {"n_rows": 2, "by_metric": {"salt_spray_hours": {"n": 2, "mean_error": 5, "rmse": 10, "mae": 8, "max_abs": 12}}}},
            {"type": "prediction_bias", "at": "2026-08-28T01:00:00Z", "bias": {"n_rows": 3, "by_metric": {"salt_spray_hours": {"n": 3, "mean_error": -2, "rmse": 60, "mae": 50, "max_abs": 70}}}},
            {"type": "other", "at": "2026-08-28T02:00:00Z"},
        ]
        await store._update_campaign(camp)  # type: ignore[attr-defined]

    # 直接用 sync 方法更简单：通过 factory 写入
    with factory() as s:
        from app.db.models import Campaign

        camp = s.get(Campaign, cid)
        camp.loop_history = [
            {"type": "prediction_bias", "at": "2026-08-28T00:00:00Z", "bias": {"n_rows": 2, "by_metric": {"salt_spray_hours": {"n": 2, "mean_error": 5, "rmse": 10, "mae": 8, "max_abs": 12}}}},
            {"type": "prediction_bias", "at": "2026-08-28T01:00:00Z", "bias": {"n_rows": 3, "by_metric": {"salt_spray_hours": {"n": 3, "mean_error": -2, "rmse": 60, "mae": 50, "max_abs": 70}}}},
        ]
        s.commit()

    r = client.get(f"/api/experiments/workbench/{cid}/bias-trend?threshold_rmse=50")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["trend"]) == 2
    assert len(body["alerts"]) == 1
    assert "60" in body["alerts"][0]


def test_bias_trend_empty_when_no_bias(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/bias2.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    reset_campaign_store(SqliteCampaignStore(factory))
    client = TestClient(app)
    plan = DOEPlan(design="lhs", factors=[], runs=[DOERun(run_id=1, coded={}, natural={"Zinc phosphate": 9.0})], notes="t", plan_id="b2", domain=ProductDomain.anticorrosion_coating)
    req = Requirement(domain=ProductDomain.anticorrosion_coating, objectives=[ObjectiveSpec(metric="salt_spray_hours", weight=1.0, direction="maximize")])
    r = client.post("/api/experiments/workbench/campaigns", json={"plan": plan.model_dump(), "requirement": req.model_dump()})
    cid = r.json()["campaign_id"]
    r = client.get(f"/api/experiments/workbench/{cid}/bias-trend")
    assert r.status_code == 200
    assert r.json()["trend"] == []
    assert r.json()["alerts"] == []

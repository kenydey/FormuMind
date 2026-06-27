"""Tests for optional baybe Campaign engine."""
from __future__ import annotations

import asyncio

import pytest

from app.db.campaign_store import SqliteCampaignStore
from app.db.database import make_engine, make_session_factory
from app.domain.schemas import (
    DOEPlan,
    DOERun,
    ExperimentRecord,
    ObjectiveSpec,
    ProductDomain,
    Requirement,
)
from app.services.engines.baybe_engine import BaybeCampaignEngine, fetch_campaign_data_for_baybe
from app.services.engines.doe_registry import baybe_available


REQ = Requirement(domain=ProductDomain.anticorrosion_coating, salt_spray_hours=500)


def _workbench_plan() -> DOEPlan:
    return DOEPlan(
        design="lhs",
        factors=[],
        runs=[DOERun(run_id=1, coded={}, natural={"Zinc phosphate": 8.0})],
        notes="test",
        plan_id="abc12345",
        domain=ProductDomain.anticorrosion_coating,
    )


@pytest.mark.skipif(not baybe_available(), reason="baybe not installed")
def test_baybe_recommend_roundtrip():
    engine = BaybeCampaignEngine()
    r1 = engine.recommend(REQ, batch_size=2)
    assert r1.engine == "baybe"
    assert len(r1.plan.runs) == 2
    assert r1.campaign_state

    records = [
        ExperimentRecord(
            domain=REQ.domain,
            factors=r1.plan.runs[0].natural,
            measured={"salt_spray_hours": 520.0},
            source="test",
        )
    ]
    r2 = engine.recommend(
        REQ,
        campaign_state=r1.campaign_state,
        measurements=records,
        batch_size=2,
    )
    assert len(r2.plan.runs) == 2
    assert r2.campaign_state != r1.campaign_state


@pytest.mark.skipif(not baybe_available(), reason="baybe not installed")
def test_baybe_recommend_with_workbench_multi_metric():
    baybe = BaybeCampaignEngine()
    db_engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(db_engine)
    store = SqliteCampaignStore(factory)
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        objectives=[
            ObjectiveSpec(metric="salt_spray_hours", weight=0.6, direction="maximize"),
            ObjectiveSpec(metric="cost_cny_per_kg", weight=0.4, direction="minimize"),
        ],
    )
    campaign = asyncio.run(store.create_from_plan(_workbench_plan(), req=req))
    rows = asyncio.run(store.list_rows(campaign.id))
    asyncio.run(
        store.batch_sync(
            campaign.id,
            [
                {
                    "id": rows[0].id,
                    "actual_params": {"Zinc phosphate": 9.0},
                    "measurements": {"salt_spray_hours": 900.0, "cost_cny_per_kg": 18.0},
                }
            ],
        )
    )

    actual_X, measurements_Y = fetch_campaign_data_for_baybe(campaign.id, req, store=store)
    assert list(measurements_Y.columns) == ["salt_spray_hours", "cost_cny_per_kg"]

    result = baybe.recommend(
        req,
        batch_size=2,
        workbench_campaign_id=campaign.id,
        store=store,
    )
    assert len(result.plan.runs) == 2
    assert result.campaign_state


def test_baybe_unavailable_raises():
    engine = BaybeCampaignEngine()
    if baybe_available():
        pytest.skip("baybe is installed")
    with pytest.raises(RuntimeError, match="not installed"):
        engine.recommend(REQ, batch_size=1)

"""Tests for campaign store sync helpers (Phase 2)."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.db.campaign_store import SqliteCampaignStore
from app.db.database import make_engine, make_session_factory
from app.domain.schemas import DOEPlan, DOERun, ProductDomain, Requirement


def _plan() -> DOEPlan:
    return DOEPlan(
        design="lhs",
        factors=[],
        runs=[DOERun(run_id=1, coded={}, natural={"Zinc phosphate": 8.0})],
        notes="test",
        plan_id="sync1234",
        domain=ProductDomain.anticorrosion_coating,
    )


def test_sqlite_sync_methods_skip_asyncio_run():
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    store = SqliteCampaignStore(factory)
    campaign = asyncio.run(store.create_from_plan(_plan()))
    with patch("app.db.campaign_store.asyncio.run", side_effect=AssertionError("asyncio.run called")):
        rows = store.list_rows_sync(campaign.id)
        assert len(rows) == 1
        assert store.get_campaign_sync(campaign.id) is not None
        assert store.get_experiments_sync(campaign.id) == []


def test_sqlite_sync_under_running_event_loop():
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    store = SqliteCampaignStore(factory)

    async def _run() -> None:
        campaign = await store.create_from_plan(_plan())
        rows = store.list_rows_sync(campaign.id)
        assert len(rows) == 1
        await store.batch_sync(
            campaign.id,
            [
                {
                    "id": rows[0].id,
                    "actual_params": {"Zinc phosphate": 9.0},
                    "measurements": {"salt_spray_hours": 800.0},
                }
            ],
        )
        completed = store.get_experiments_sync(campaign.id)
        assert len(completed) == 1

    asyncio.run(_run())


def test_fetch_campaign_data_uses_sqlite_sync_path():
    pd = pytest.importorskip("pandas")
    from app.services.engines.baybe_engine import fetch_campaign_data_for_baybe

    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    store = SqliteCampaignStore(factory)
    req = Requirement(domain=ProductDomain.anticorrosion_coating)
    campaign = asyncio.run(store.create_from_plan(_plan(), req=req))
    rows = store.list_rows_sync(campaign.id)
    asyncio.run(
        store.batch_sync(
            campaign.id,
            [
                {
                    "id": rows[0].id,
                    "actual_params": {"Zinc phosphate": 9.0},
                    "measurements": {"salt_spray_hours": 900.0},
                }
            ],
        )
    )
    with patch("app.db.campaign_store.asyncio.run", side_effect=AssertionError("asyncio.run called")):
        actual_X, measurements_Y = fetch_campaign_data_for_baybe(campaign.id, req, store=store)
    assert not actual_X.empty
    assert "salt_spray_hours" in measurements_Y.columns

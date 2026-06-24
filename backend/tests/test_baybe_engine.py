"""Tests for optional baybe Campaign engine."""
from __future__ import annotations

import pytest

from app.domain.schemas import ExperimentRecord, ProductDomain, Requirement
from app.services.engines.baybe_engine import BaybeCampaignEngine
from app.services.engines.doe_registry import baybe_available


REQ = Requirement(domain=ProductDomain.anticorrosion_coating, salt_spray_hours=500)


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


def test_baybe_unavailable_raises():
    engine = BaybeCampaignEngine()
    if baybe_available():
        pytest.skip("baybe is installed")
    with pytest.raises(RuntimeError, match="not installed"):
        engine.recommend(REQ, batch_size=1)

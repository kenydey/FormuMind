"""Datalab Headless API payload contract — blocktype + sample type."""
from __future__ import annotations

import json

import httpx
import pytest

from app.db.campaign_store import DatalabCampaignStore, _blocks_for_row
from app.db.datalab_client import DATALAB_BLOCK_KIND, DATALAB_SAMPLE_TYPE, datalab_sample_type
from app.db.database import make_engine, make_session_factory
from app.domain.schemas import DOEPlan, DOERun, ProductDomain


def test_datalab_blocks_use_blocktype_not_block_type():
    blocks = _blocks_for_row(
        planned_params={"Zinc phosphate": 8.0},
        actual_params={"Zinc phosphate": 8.0},
        measurements={"salt_spray_hours": None},
        status="Pending",
    )
    for block in blocks.values():
        assert "blocktype" in block
        assert block["blocktype"] == DATALAB_BLOCK_KIND
        assert "block_type" not in block


@pytest.mark.asyncio
async def test_datalab_sample_payload_contract(tmp_path):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/new-sample/":
            captured.update(json.loads(request.content))
            item_id = captured["new_sample_data"]["item_id"]
            return httpx.Response(
                200,
                json={"sample_list_entry": {"item_id": item_id, "name": "n"}},
            )
        return httpx.Response(404)

    db_path = tmp_path / "contract.db"
    factory = make_session_factory(make_engine(f"sqlite:///{db_path}"))
    client = httpx.AsyncClient(
        base_url="http://datalab.test",
        transport=httpx.MockTransport(handler),
    )
    store = DatalabCampaignStore("http://datalab.test", factory)
    store._client = client

    plan = DOEPlan(
        design="lhs",
        factors=[],
        runs=[DOERun(run_id=1, coded={}, natural={"Zinc phosphate": 8.0})],
        notes="contract",
        plan_id="c01",
        domain=ProductDomain.anticorrosion_coating,
    )

    try:
        await store.create_from_plan(plan, strategy="DOE-lhs")
    finally:
        await store.close()

    sample = captured["new_sample_data"]
    assert sample["type"] == DATALAB_SAMPLE_TYPE
    assert sample["type"] == datalab_sample_type()
    assert isinstance(sample["type"], str)
    for block in sample["blocks_obj"].values():
        assert block.get("blocktype") == "comment"

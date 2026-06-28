"""DatalabCampaignStore — saga rollback, Pydantic validation, connection pool."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx
import pytest

from app.db.campaign_store import DatalabCampaignStore
from app.db.datalab_client import (
    DatalabStoreError,
    DatalabUnavailableError,
    parse_create_sample_response,
    parse_item_envelope,
)
from app.db.database import make_engine, make_session_factory
from app.domain.schemas import DOEPlan, DOERun, ProductDomain

_PARAMS = "formumind_params"
_MEASUREMENTS = "formumind_measurements"


def _plan(*, runs: int = 2) -> DOEPlan:
    return DOEPlan(
        design="lhs",
        factors=[],
        runs=[
            DOERun(run_id=i, coded={}, natural={"Zinc phosphate": 8.0 + i})
            for i in range(1, runs + 1)
        ],
        notes="datalab-test",
        plan_id="dltest01",
        domain=ProductDomain.anticorrosion_coating,
    )


def _item_data(*, status: str = "Pending") -> dict:
    planned = {"Zinc phosphate": 8.0}
    return {
        "blocks_obj": {
            _PARAMS: {
                "block_id": _PARAMS,
                "block_type": "generic",
                "data": {
                    "planned_params": planned,
                    "actual_params": dict(planned),
                    "status": status,
                },
            },
            _MEASUREMENTS: {
                "block_id": _MEASUREMENTS,
                "block_type": "generic",
                "data": {"salt_spray_hours": None, "cost_cny_per_kg": None},
            },
        }
    }


@dataclass
class MockDatalabState:
    created: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    create_calls: int = 0
    fail_on_create_call: int | None = None
    invalid_item_blocks: bool = False


def _mock_handler(state: MockDatalabState):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/new-sample/":
            state.create_calls += 1
            if state.fail_on_create_call and state.create_calls == state.fail_on_create_call:
                return httpx.Response(500, json={"error": "simulated datalab failure"})
            body = json.loads(request.content)
            item_id = body["new_sample_data"]["item_id"]
            state.created.append(item_id)
            return httpx.Response(
                200,
                json={"sample_list_entry": {"item_id": item_id, "name": body["new_sample_data"]["name"]}},
            )
        if path.startswith("/get-item-data/"):
            item_id = path.rsplit("/", 1)[-1]
            if state.invalid_item_blocks:
                return httpx.Response(200, json={"item_data": {"blocks_obj": {}}})
            return httpx.Response(200, json={"item_data": _item_data()})
        if path == "/delete-sample/":
            body = json.loads(request.content)
            state.deleted.append(body["item_id"])
            return httpx.Response(200, json={"status": "success"})
        if path == "/save-item/":
            return httpx.Response(200, json={"status": "success"})
        return httpx.Response(404, json={"error": f"unmocked {path}"})

    return handler


async def _store_with_mock(tmp_path, state: MockDatalabState | None = None) -> DatalabCampaignStore:
    state = state or MockDatalabState()
    db_path = tmp_path / "datalab_campaign.db"
    factory = make_session_factory(make_engine(f"sqlite:///{db_path}"))
    client = httpx.AsyncClient(
        base_url="http://datalab.test",
        transport=httpx.MockTransport(_mock_handler(state)),
    )
    store = DatalabCampaignStore("http://datalab.test", factory)
    store._client = client
    return store


@pytest.mark.asyncio
async def test_create_from_plan_success(tmp_path):
    state = MockDatalabState()
    store = await _store_with_mock(tmp_path, state)
    try:
        campaign = await store.create_from_plan(_plan())
        assert campaign.id >= 1
        assert len(campaign.sample_refs) == 2
        assert len(state.created) == 2
        assert state.deleted == []

        rows = await store.list_rows(campaign.id)
        assert len(rows) == 2
        assert rows[0].status == "Pending"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_create_from_plan_saga_rollback_on_mid_failure(tmp_path):
    state = MockDatalabState(fail_on_create_call=2)
    store = await _store_with_mock(tmp_path, state)
    try:
        with pytest.raises(DatalabUnavailableError, match="500 Internal Server Error"):
            await store.create_from_plan(_plan())

        assert len(state.created) == 1
        assert set(state.created).issubset(state.deleted)
        assert len(state.deleted) == 2

        with store._session_factory() as session:
            from app.db.models import Campaign

            assert session.query(Campaign).count() == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_create_from_plan_saga_rollback_on_item_id_mismatch(tmp_path):
    state = MockDatalabState()

    def bad_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/new-sample/":
            state.create_calls += 1
            body = json.loads(request.content)
            item_id = body["new_sample_data"]["item_id"]
            state.created.append(item_id)
            return httpx.Response(
                200,
                json={"sample_list_entry": {"item_id": "wrong-id", "name": "x"}},
            )
        if request.url.path == "/delete-sample/":
            body = json.loads(request.content)
            state.deleted.append(body["item_id"])
            return httpx.Response(200, json={"status": "success"})
        return httpx.Response(404)

    db_path = tmp_path / "mismatch.db"
    factory = make_session_factory(make_engine(f"sqlite:///{db_path}"))
    client = httpx.AsyncClient(base_url="http://datalab.test", transport=httpx.MockTransport(bad_handler))
    store = DatalabCampaignStore("http://datalab.test", factory)
    store._client = client
    try:
        with pytest.raises(DatalabUnavailableError, match="item_id mismatch"):
            await store.create_from_plan(_plan(runs=1))
        assert state.deleted == state.created
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_list_rows_raises_on_invalid_blocks(tmp_path):
    state = MockDatalabState(invalid_item_blocks=True)
    store = await _store_with_mock(tmp_path, state)
    try:
        campaign = await store.create_from_plan(_plan(runs=1))
        with pytest.raises(DatalabStoreError, match="formumind_params"):
            await store.list_rows(campaign.id)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_ensure_client_reuses_connection(tmp_path):
    store = await _store_with_mock(tmp_path)
    try:
        c1 = await store._ensure_client()
        c2 = await store._ensure_client()
        assert c1 is c2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_close_releases_client(tmp_path):
    store = await _store_with_mock(tmp_path)
    client = await store._ensure_client()
    await store.close()
    assert store._client is None
    assert client.is_closed


def test_parse_create_sample_response_valid():
    sample = parse_create_sample_response({"item_id": "abc", "name": "n"}, "abc")
    assert sample.item_id == "abc"


def test_parse_create_sample_response_mismatch():
    with pytest.raises(DatalabStoreError, match="item_id mismatch"):
        parse_create_sample_response({"item_id": "wrong"}, "expected")


def test_parse_item_envelope_requires_blocks():
    with pytest.raises(DatalabStoreError):
        parse_item_envelope({"item_data": {"blocks_obj": {}}}, required_blocks=(_PARAMS, _MEASUREMENTS))

    item = parse_item_envelope(
        {"item_data": _item_data()},
        required_blocks=(_PARAMS, _MEASUREMENTS),
    )
    assert _PARAMS in item["blocks_obj"]

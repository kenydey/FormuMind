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
                "blocktype": "comment",
                "data": {
                    "planned_params": planned,
                    "actual_params": dict(planned),
                    "status": status,
                },
            },
            _MEASUREMENTS: {
                "block_id": _MEASUREMENTS,
                "blocktype": "comment",
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
    missing_item_ids: set[str] = field(default_factory=set)
    saved_items: dict[str, dict] = field(default_factory=dict)


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
            if item_id in state.missing_item_ids:
                return httpx.Response(404, json={"error": "item not found"})
            if item_id in state.saved_items:
                return httpx.Response(200, json={"item_data": state.saved_items[item_id]})
            if state.invalid_item_blocks:
                return httpx.Response(200, json={"item_data": {"blocks_obj": {}}})
            return httpx.Response(200, json={"item_data": _item_data()})
        if path == "/delete-sample/":
            body = json.loads(request.content)
            state.deleted.append(body["item_id"])
            return httpx.Response(200, json={"status": "success"})
        if path == "/save-item/":
            body = json.loads(request.content)
            state.saved_items[body["item_id"]] = body["data"]
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
        # C16: 回滚只删「实际创建成功」的 sample——失败的那次从未创建，
        # 删除会是无意义的假性清理。故 deleted 仅含 1 个已创建项。
        assert len(state.deleted) == 1

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
            # 模拟真实 Datalab：实际创建的是响应里返回的 id（与请求 id 不符）
            state.created.append("wrong-id")
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
async def test_list_rows_skips_item_deleted_from_datalab(tmp_path):
    """A single sample removed from Datalab (e.g. deleted directly in the ELN
    UI) must not 500 the whole campaign — the other, still-live rows should
    still come back."""
    state = MockDatalabState()
    store = await _store_with_mock(tmp_path, state)
    try:
        campaign = await store.create_from_plan(_plan(runs=2))
        deleted_item_id = campaign.sample_refs[0]["item_id"]
        state.missing_item_ids.add(deleted_item_id)

        rows = await store.list_rows(campaign.id)

        assert len(rows) == 1
        assert rows[0].item_id != deleted_item_id
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_batch_sync_survives_item_deleted_from_datalab(tmp_path):
    """batch_sync's own careful per-row skip must not be undone by its final
    unguarded list_rows() call when one referenced item is gone."""
    state = MockDatalabState()
    store = await _store_with_mock(tmp_path, state)
    try:
        campaign = await store.create_from_plan(_plan(runs=2))
        deleted_ref = campaign.sample_refs[0]
        live_ref = campaign.sample_refs[1]
        state.missing_item_ids.add(deleted_ref["item_id"])

        updated, rows = await store.batch_sync(
            campaign.id,
            [
                {"id": deleted_ref["id"], "status": "Completed", "measurements": {}},
                {"id": live_ref["id"], "status": "Completed", "measurements": {}},
            ],
        )

        assert updated == 1
        assert len(rows) == 1
        assert rows[0].item_id == live_ref["item_id"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_batch_sync_persists_note_and_tags(tmp_path):
    """note/tags must round-trip through the Datalab backend the same way
    they do through SqliteCampaignStore, not silently drop on every sync."""
    state = MockDatalabState()
    store = await _store_with_mock(tmp_path, state)
    try:
        campaign = await store.create_from_plan(_plan(runs=1))
        ref = campaign.sample_refs[0]

        _, rows = await store.batch_sync(
            campaign.id,
            [{"id": ref["id"], "note": "check viscosity", "tags": ["urgent", "retest"]}],
        )
        assert rows[0].note == "check viscosity"
        assert rows[0].tags == ["urgent", "retest"]

        # A later sync that doesn't mention note/tags must preserve them.
        _, rows2 = await store.batch_sync(
            campaign.id,
            [{"id": ref["id"], "status": "Completed"}],
        )
        assert rows2[0].note == "check viscosity"
        assert rows2[0].tags == ["urgent", "retest"]
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


@pytest.mark.asyncio
async def test_batch_sync_all_fail_raises(tmp_path, monkeypatch):
    """A3: 整批行全部失败（疑似 Datalab 不可达）不应静默返回 0，必须 raise。"""
    state = MockDatalabState()
    store = await _store_with_mock(tmp_path, state)
    try:
        campaign = await store.create_from_plan(_plan(runs=2))
        refs = campaign.sample_refs

        async def _boom(_item_id):
            raise RuntimeError("datalab down")

        monkeypatch.setattr(store, "_get_item", _boom)
        with pytest.raises(DatalabUnavailableError, match="全部 2 行失败"):
            await store.batch_sync(
                campaign.id,
                [
                    {"id": refs[0]["id"], "status": "Completed", "measurements": {}},
                    {"id": refs[1]["id"], "status": "Completed", "measurements": {}},
                ],
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_batch_sync_partial_fail_does_not_raise(tmp_path, monkeypatch):
    """部分失败不应 raise，成功的行仍应返回。"""
    state = MockDatalabState()
    store = await _store_with_mock(tmp_path, state)
    try:
        campaign = await store.create_from_plan(_plan(runs=2))
        refs = campaign.sample_refs
        orig_get = store._get_item
        call_count = {"n": 0}

        async def _flaky(item_id):
            # 仅前 2 次调用（batch_sync 循环内）对第一个 item 失败；
            # 后续 list_rows 刷新时恢复正常，避免副作用污染断言
            call_count["n"] += 1
            if call_count["n"] <= 2 and item_id == refs[0]["item_id"]:
                raise RuntimeError("flaky")
            return await orig_get(item_id)

        monkeypatch.setattr(store, "_get_item", _flaky)
        updated, rows = await store.batch_sync(
            campaign.id,
            [
                {"id": refs[0]["id"], "status": "Completed", "measurements": {}},
                {"id": refs[1]["id"], "status": "Completed", "measurements": {}},
            ],
        )
        assert updated == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_reconcile_prunes_stale_refs(tmp_path):
    """reconcile_sample_refs removes refs whose Datalab item returns a definitive 404."""
    state = MockDatalabState()
    store = await _store_with_mock(tmp_path, state)
    try:
        campaign = await store.create_from_plan(_plan(runs=3))
        deleted_item_id = campaign.sample_refs[1]["item_id"]
        state.missing_item_ids.add(deleted_item_id)

        result = await store.reconcile_sample_refs(campaign.id)

        assert result["removed"] == [deleted_item_id]
        assert result["removed_count"] == 1
        assert result["errors"] == []

        # sample_refs persisted: stale ref gone, others kept
        refreshed = store.get_campaign_sync(campaign.id)
        remaining_ids = {str(r["item_id"]) for r in refreshed.sample_refs}
        assert deleted_item_id not in remaining_ids
        assert len(remaining_ids) == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_reconcile_idempotent(tmp_path):
    """A second reconcile on a clean campaign returns an empty removed list."""
    state = MockDatalabState()
    store = await _store_with_mock(tmp_path, state)
    try:
        campaign = await store.create_from_plan(_plan(runs=2))
        deleted_item_id = campaign.sample_refs[0]["item_id"]
        state.missing_item_ids.add(deleted_item_id)

        first = await store.reconcile_sample_refs(campaign.id)
        second = await store.reconcile_sample_refs(campaign.id)

        assert first["removed_count"] == 1
        assert second["removed"] == []
        assert second["removed_count"] == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_list_rows_auto_prunes_stale_refs(tmp_path):
    """list_rows prunes definitive 404 refs so subsequent reads stop warning."""
    state = MockDatalabState()
    store = await _store_with_mock(tmp_path, state)
    try:
        campaign = await store.create_from_plan(_plan(runs=2))
        deleted_item_id = campaign.sample_refs[0]["item_id"]
        state.missing_item_ids.add(deleted_item_id)

        rows = await store.list_rows(campaign.id)
        assert len(rows) == 1  # live row still returned

        refreshed = store.get_campaign_sync(campaign.id)
        remaining_ids = {str(r["item_id"]) for r in refreshed.sample_refs}
        assert deleted_item_id not in remaining_ids
        assert len(remaining_ids) == 1
    finally:
        await store.close()

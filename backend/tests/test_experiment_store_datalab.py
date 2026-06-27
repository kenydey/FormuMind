"""DatalabExperimentStore — saga rollback, Pydantic validation, sync connection pool."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx
import pytest

from app.db.datalab_client import DatalabStoreError, parse_create_sample_response
from app.db.database import make_engine, make_session_factory
from app.db.store import DatalabExperimentStore, _blocks_for_training
from app.domain.schemas import ExperimentRecord, ProductDomain
from app.services.training import ModelRegistry

_TRAINING = "formumind_training"


def _record(*, zinc: float = 8.0, salt_spray: float = 840.0) -> ExperimentRecord:
    return ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating,
        project_id="proj_test",
        factors={"Zinc phosphate": zinc, "Bisphenol-A epoxy (DGEBA)": 38.0},
        cure_temperature_c=80.0,
        measured={"salt_spray_hours": salt_spray},
        source="test",
        label="unit-test",
    )


def _item_data_from_record(rec: ExperimentRecord) -> dict:
    return {"blocks_obj": _blocks_for_training(rec)}


@dataclass
class MockDatalabState:
    created: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    create_calls: int = 0
    fail_on_create_call: int | None = None
    invalid_training_block: bool = False


def _mock_handler(state: MockDatalabState):
    stored: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/new-sample/":
            state.create_calls += 1
            if state.fail_on_create_call and state.create_calls == state.fail_on_create_call:
                return httpx.Response(500, json={"error": "simulated datalab failure"})
            body = json.loads(request.content)
            item_id = body["new_sample_data"]["item_id"]
            state.created.append(item_id)
            stored[item_id] = body["new_sample_data"]
            return httpx.Response(
                200,
                json={"sample_list_entry": {"item_id": item_id, "name": body["new_sample_data"]["name"]}},
            )
        if path.startswith("/get-item-data/"):
            item_id = path.rsplit("/", 1)[-1]
            if state.invalid_training_block:
                return httpx.Response(200, json={"item_data": {"blocks_obj": {}}})
            return httpx.Response(200, json={"item_data": stored.get(item_id, _item_data_from_record(_record()))})
        if path == "/delete-sample/":
            body = json.loads(request.content)
            state.deleted.append(body["item_id"])
            stored.pop(body["item_id"], None)
            return httpx.Response(200, json={"status": "success"})
        return httpx.Response(404, json={"error": f"unmocked {path}"})

    return handler


def _store_with_mock(tmp_path, state: MockDatalabState | None = None) -> DatalabExperimentStore:
    state = state or MockDatalabState()
    db_path = tmp_path / "datalab_exp.db"
    factory = make_session_factory(make_engine(f"sqlite:///{db_path}"))
    client = httpx.Client(
        base_url="http://datalab.test",
        transport=httpx.MockTransport(_mock_handler(state)),
    )
    store = DatalabExperimentStore("http://datalab.test", factory)
    store._client = client
    return store


def test_add_and_all_round_trip(tmp_path):
    store = _store_with_mock(tmp_path)
    try:
        store.add([_record(zinc=6.0, salt_spray=680.0), _record(zinc=10.0, salt_spray=1000.0)])
        rows = store.all()
        assert len(rows) == 2
        assert rows[0].project_id == "proj_test"
        assert rows[0].measured["salt_spray_hours"] == 680.0
        assert store.count() == 2
    finally:
        store.close()


def test_add_saga_rollback_on_mid_failure(tmp_path):
    state = MockDatalabState(fail_on_create_call=2)
    store = _store_with_mock(tmp_path, state)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            store.add([_record(), _record(zinc=10.0)])
        assert len(state.created) == 1
        assert set(state.created).issubset(state.deleted)
        assert store.count() == 0
    finally:
        store.close()


def test_add_saga_rollback_on_item_id_mismatch(tmp_path):
    state = MockDatalabState()

    def bad_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/new-sample/":
            state.create_calls += 1
            body = json.loads(request.content)
            item_id = body["new_sample_data"]["item_id"]
            state.created.append(item_id)
            return httpx.Response(200, json={"sample_list_entry": {"item_id": "wrong-id", "name": "x"}})
        if request.url.path == "/delete-sample/":
            body = json.loads(request.content)
            state.deleted.append(body["item_id"])
            return httpx.Response(200, json={"status": "success"})
        return httpx.Response(404)

    db_path = tmp_path / "mismatch.db"
    factory = make_session_factory(make_engine(f"sqlite:///{db_path}"))
    client = httpx.Client(base_url="http://datalab.test", transport=httpx.MockTransport(bad_handler))
    store = DatalabExperimentStore("http://datalab.test", factory)
    store._client = client
    try:
        with pytest.raises(DatalabStoreError, match="item_id mismatch"):
            store.add([_record()])
        assert state.deleted == state.created
        assert store.count() == 0
    finally:
        store.close()


def test_all_raises_on_invalid_training_block(tmp_path):
    state = MockDatalabState(invalid_training_block=True)
    store = _store_with_mock(tmp_path, state)
    try:
        store.add([_record()])
        with pytest.raises(DatalabStoreError, match="formumind_training"):
            store.all()
    finally:
        store.close()


def test_clear_deletes_samples_and_index(tmp_path):
    store = _store_with_mock(tmp_path)
    try:
        store.add([_record(), _record(zinc=9.0)])
        assert store.count() == 2
        store.clear()
        assert store.count() == 0
        assert store.all() == []
    finally:
        store.close()


def test_client_reuse_and_close(tmp_path):
    store = _store_with_mock(tmp_path)
    try:
        c1 = store._ensure_client()
        c2 = store._ensure_client()
        assert c1 is c2
        store.close()
        assert store._client is None
        assert c1.is_closed
    finally:
        store.close()


def test_model_registry_with_datalab_store(tmp_path):
    store = _store_with_mock(tmp_path)
    try:
        reg = ModelRegistry(store=store)
        records = [_record(zinc=float(z), salt_spray=200.0 + 80.0 * z) for z in range(2, 14)]
        reg.add(records)
        assert reg.total_records == 12
        assert any(i.metric == "salt_spray_hours" for i in reg.info())
    finally:
        store.close()


def test_parse_create_sample_response_valid():
    sample = parse_create_sample_response({"item_id": "abc", "name": "n"}, "abc")
    assert sample.item_id == "abc"

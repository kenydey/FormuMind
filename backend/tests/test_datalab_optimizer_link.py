"""P0-2: Datalab -> 优化器链路测试 (mock Datalab, 不碰真实 ELN)。

覆盖:
1. apply_lab_measurements: 明细行 + 父 ExperimentRow.measured 回写。
2. sync 后 SqlExperimentStore 可见 lab 数据 (优化器消费点)。
3. lab_measurement_source: 有 lab 实测 -> "lab", 否则 "predictor_virtual"。
4. block_experiment_id 冲突检测。
5. POST /api/experiments/{id}/sync-datalab: 404 / 409(无item_id) /
   409(块归属冲突) / fail-open / happy path。
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

import app.db.database as db_mod
from app.db.database import Base, make_engine, make_session_factory
from app.db.measurement_store import MeasurementStore
from app.db.models import ExperimentRow
from app.domain.schemas import ExperimentRecord, Measurement, ProductDomain
from app.services.datalab_sync import (
    MEASUREMENT_BLOCK_ID,
    apply_lab_measurements,
    block_experiment_id,
    sync_datalab_to_store,
    sync_item_data_to_store,
)
from app.services.doe_cycle_service import lab_measurement_source


@pytest.fixture()
def factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/link.db")
    Base.metadata.create_all(engine)
    fac = make_session_factory(engine)
    # 模拟 doe_cycle_service 建的行: item_id=None, measured={}, source="lab"
    with fac() as session:
        session.add(
            ExperimentRow(
                domain="degreaser", project_id="", factors={"a": 1.0},
                measured={}, source="lab",
            )
        )
        session.commit()
    return fac


@pytest.fixture()
def _fresh_registry():
    """隔离全局 training registry/store: 每个 API 测试前后重置懒加载单例。

    注意 app/db/store.py 的 get_experiment_store() 另有模块级 _store 缓存,
    只清 registry._registry 不够, 必须一起清, 否则 store 仍指向旧测试的库。
    """
    from app.db import store as _store_mod
    from app.services.training import registry

    registry._registry = None
    _store_mod._store = None
    yield
    registry._registry = None
    _store_mod._store = None


def _item_envelope(measurements, experiment_id=1):
    return {
        "item_id": "test:ABC123",
        "item_data": {
            "blocks_obj": {
                MEASUREMENT_BLOCK_ID: {
                    "block_id": MEASUREMENT_BLOCK_ID,
                    "blocktype": "comment",
                    "data": {"experiment_id": experiment_id, "measurements": measurements},
                }
            }
        },
    }


def _transport_for(body, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/items/test:ABC123/"
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


_MEAS = [
    {"metric": "salt_spray_hours", "value": 720.0, "unit": "h",
     "test_method": "ASTM B117"},
]


def test_apply_lab_measurements_mirrors_to_parent(factory):
    rep = apply_lab_measurements(
        1, [Measurement.model_validate(_MEAS[0])], session_factory=factory
    )
    assert rep["written"] == 1
    assert rep["mirrored"] is True
    assert rep["errors"] == []

    with factory() as session:
        row = session.get(ExperimentRow, 1)
        assert row.measured == {"salt_spray_hours": 720.0}
        stored = MeasurementStore(factory).for_experiment(1)
    assert len(stored) == 1
    assert stored[0].test_method == "ASTM B117"


def test_apply_lab_measurements_missing_parent(factory):
    rep = apply_lab_measurements(
        999, [Measurement.model_validate(_MEAS[0])], session_factory=factory
    )
    # 父行不存在: fail-open, 不抛异常
    assert rep["mirrored"] is False
    assert rep["errors"] != []


def test_sync_end_to_end_visible_to_registry(factory, monkeypatch):
    """sync 后 registry 可见 lab 数据 (优化器消费点)。"""
    import app.db.database as _db

    monkeypatch.setattr(_db, "default_session_factory", lambda: factory)

    t = _transport_for(_item_envelope(_MEAS))
    report = sync_datalab_to_store(
        "http://datalab:5001", "test:ABC123", 1,
        session_factory=factory, _transport=t,
    )
    assert report["synced"] == 1
    assert report["mirrored"] is True
    assert report["validated"] is None

    # 模拟 load_prior_measurements 的 registry 路径
    from app.db.store import SqlExperimentStore

    store = SqlExperimentStore(factory)
    recs = store.all()
    assert len(recs) == 1
    assert recs[0].source == "lab"
    assert recs[0].measured == {"salt_spray_hours": 720.0}
    assert lab_measurement_source(recs) == "lab"


def test_block_experiment_id():
    assert block_experiment_id(_item_envelope(_MEAS, experiment_id=7)["item_data"]) == 7
    # 块存在但 measurements 为空: 仍返回块声明的 experiment_id (供冲突检测)
    assert block_experiment_id(_item_envelope(None)["item_data"]) == 1
    assert block_experiment_id(None) is None
    assert block_experiment_id({}) is None


def test_lab_measurement_source_labels():
    lab = ExperimentRecord(
        domain=ProductDomain.degreaser, factors={"a": 1.0},
        measured={"salt_spray_hours": 720.0}, source="lab",
    )
    assert lab_measurement_source([lab]) == "lab"

    virtual = ExperimentRecord(
        domain=ProductDomain.degreaser, factors={"a": 1.0},
        measured={"salt_spray_hours": 100.0}, source="baybe_opt",
    )
    assert lab_measurement_source([virtual]) == "predictor_virtual"
    assert lab_measurement_source([]) == "predictor_virtual"
    # 有 lab 行但无实测值 -> 仍视为 virtual (无依据)
    empty_lab = ExperimentRecord(
        domain=ProductDomain.degreaser, factors={"a": 1.0}, source="lab"
    )
    assert lab_measurement_source([empty_lab]) == "predictor_virtual"


def _app_client(tmp_path, monkeypatch):
    from tests.alembic_helpers import run_upgrade

    db_url = f"sqlite:///{tmp_path}/api_link.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUMIND_DB_URL", db_url)
    db_mod._default.clear()
    from app.main import app

    return TestClient(app)


def _create_experiment(client):
    r = client.post(
        "/api/experiments",
        json={"records": [
            {"domain": "degreaser", "factors": {"a": 1.0},
             "measured": {}, "source": "lab"}
        ], "retrain": False},
    )
    assert r.status_code == 200, r.text
    rows = client.get("/api/experiments").json()
    return max(row["id"] for row in rows)


def _set_item_id(exp_id, item_id):
    fac = db_mod.default_session_factory()
    with fac() as s:
        row = s.get(ExperimentRow, exp_id)
        row.item_id = item_id
        s.commit()


def test_sync_endpoint_404(tmp_path, monkeypatch, _fresh_registry):
    client = _app_client(tmp_path, monkeypatch)
    with client:
        r = client.post("/api/experiments/999999/sync-datalab")
    assert r.status_code == 404


def test_sync_endpoint_409_without_item_id(tmp_path, monkeypatch, _fresh_registry):
    client = _app_client(tmp_path, monkeypatch)
    with client:
        exp_id = _create_experiment(client)
        r2 = client.post(f"/api/experiments/{exp_id}/sync-datalab")
    assert r2.status_code == 409, r2.text


def test_sync_endpoint_409_on_block_conflict(tmp_path, monkeypatch, _fresh_registry):
    """块内 experiment_id 与调用方不一致 -> 409, 不写库。"""
    import app.services.datalab_sync as dl_mod

    client = _app_client(tmp_path, monkeypatch)
    with client:
        exp_id = _create_experiment(client)
        _set_item_id(exp_id, "test:ABC123")
        monkeypatch.setattr(
            dl_mod, "fetch_item_data",
            lambda *a, **k: _item_envelope(_MEAS, experiment_id=exp_id + 100)["item_data"],
        )
        r = client.post(f"/api/experiments/{exp_id}/sync-datalab")
    assert r.status_code == 409, r.text
    fac = db_mod.default_session_factory()
    with fac() as s:
        assert s.get(ExperimentRow, exp_id).measured == {}


def test_sync_endpoint_happy_path(tmp_path, monkeypatch, _fresh_registry):
    """mock Datalab: 同步成功 -> measured 回写 + registry 可见。"""
    import app.services.datalab_sync as dl_mod

    client = _app_client(tmp_path, monkeypatch)
    with client:
        exp_id = _create_experiment(client)
        _set_item_id(exp_id, "test:ABC123")
        envelope = _item_envelope(_MEAS, experiment_id=exp_id)
        monkeypatch.setattr(
            dl_mod, "fetch_item_data", lambda *a, **k: envelope["item_data"]
        )
        r = client.post(f"/api/experiments/{exp_id}/sync-datalab")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["synced"] == 1
    assert body["mirrored"] is True
    assert body["errors"] == []
    assert body["validated"] is None
    assert body["registry_refreshed"] is True

    fac = db_mod.default_session_factory()
    with fac() as s:
        row = s.get(ExperimentRow, exp_id)
        assert row.measured == {"salt_spray_hours": 720.0}


def test_sync_endpoint_fail_open_when_datalab_down(tmp_path, monkeypatch, _fresh_registry):
    """Datalab 不可达 -> 200 + errors, 不抛 500。"""
    client = _app_client(tmp_path, monkeypatch)
    with client:
        exp_id = _create_experiment(client)
        _set_item_id(exp_id, "test:ABC123")
        # Datalab 地址指向不可达端口 -> fetch_item_data fail-open 返回 None
        monkeypatch.setenv("FORMUMIND_DATALAB_API_URL", "http://127.0.0.1:9")
        from app.config import get_settings

        get_settings.cache_clear()
        r2 = client.post(f"/api/experiments/{exp_id}/sync-datalab")
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["synced"] == 0
    assert body["errors"] != []
    assert body["validated"] is None
    assert body["registry_refreshed"] is False

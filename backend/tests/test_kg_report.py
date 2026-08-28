"""E 台账审计报表：零增长告警分支."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.db.database import Base, make_engine, make_session_factory
from app.db.entity_store import EntityStore


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_TASK_DIR", str(tmp_path / "e_report"))
    monkeypatch.setenv("FORMUMIND_TASK_PROGRESS_DIR", str(tmp_path / "e_report" / "progress"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _store(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path}/e_report.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    store = EntityStore(factory)
    import app.db.entity_store as es_mod

    monkeypatch.setattr(es_mod, "_store", store)
    return store


def test_report_alert_when_zero(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    client = TestClient(app)
    r = client.get("/api/kg/feedback/report")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["measured_performance"] == 0
    assert body["alert"] is not None
    assert "实测回流" in body["alert"]


def test_report_no_alert_when_has_measured(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    with store._session_factory() as s:
        store.upsert_entity(s, id="dom", canonical_name="anticorrosion_coating", kind="domain")
        store.upsert_entity(s, id="prop", canonical_name="salt_spray_hours", kind="property")
    with store._session_factory() as s:
        store.merge_semantic_link(s, src_entity_id="dom", dst_entity_id="prop", link_type="measured_performance", confidence=0.8, evidence_ref={"source_id": "measured:campaign_1", "extraction_method": "measured", "sentence": "x"}, extraction_method="measured")
    client = TestClient(app)
    r = client.get("/api/kg/feedback/report")
    assert r.status_code == 200
    body = r.json()
    assert body["measured_performance"] == 1
    assert body["alert"] is None

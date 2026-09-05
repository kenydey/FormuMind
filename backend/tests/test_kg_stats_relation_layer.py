"""B10: /api/kg/stats must flag an empty relation layer (entities>0, links=0)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_TASK_DIR", str(tmp_path / "kg_stats"))
    monkeypatch.setenv("FORMUMIND_TASK_PROGRESS_DIR", str(tmp_path / "kg_stats" / "progress"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _fake_store(payload: dict):
    class _Fake:
        def stats(self):
            return payload

    return _Fake()


def test_kg_stats_flags_empty_relation_layer(monkeypatch):
    import app.db.entity_store as es_mod

    monkeypatch.setattr(
        es_mod,
        "get_entity_store",
        lambda: _fake_store(
            {
                "entities": 715,
                "mentions": 70000,
                "links": 0,
                "entities_by_kind": {"chemical": 715},
                "links_by_type": {},
            }
        ),
    )
    r = TestClient(app).get("/api/kg/stats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert body["entities"] == 715
    assert body["links"] == 0
    assert body["relation_layer_empty"] is True
    assert body["warnings"]
    assert any("关系" in w or "kb_entity_links" in w for w in body["warnings"])


def test_kg_stats_clear_when_links_present(monkeypatch):
    import app.db.entity_store as es_mod

    monkeypatch.setattr(
        es_mod,
        "get_entity_store",
        lambda: _fake_store(
            {
                "entities": 10,
                "mentions": 50,
                "links": 12,
                "entities_by_kind": {"chemical": 10},
                "links_by_type": {"inhibits": 4, "synergizes": 8},
            }
        ),
    )
    body = TestClient(app).get("/api/kg/stats").json()
    assert body["relation_layer_empty"] is False
    assert body["warnings"] == []
    assert body["links"] == 12


def test_kg_stats_disabled_when_kg_off(monkeypatch):
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "false")
    get_settings.cache_clear()
    body = TestClient(app).get("/api/kg/stats").json()
    assert body["enabled"] is False
    assert body["relation_layer_empty"] is False

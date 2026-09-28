"""W6-3 / P2-3 Smart Collections API: CRUD + refresh endpoints."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services import smart_collections as sc
from app.services import literature_manifest as lm


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "_data_root", lambda: tmp_path)
    monkeypatch.setattr(lm, "_data_root", lambda: tmp_path)
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture()
def no_search(monkeypatch):
    monkeypatch.setattr(sc, "_run_search", lambda q, f, settings=None: [])
    return True


def _client() -> TestClient:
    from app.main import app

    return TestClient(app)


def test_api_crud_and_refresh(data_dir, no_search):
    c = _client()
    r = c.post(
        "/api/collections",
        json={
            "project_id": "p1",
            "name": "VIANT",
            "query": "waterborne conversion coating",
            "filters": {"date_from": 2020},
            "schedule": {"enabled": True, "interval_hours": 12},
        },
    )
    assert r.status_code == 201, r.text
    cid = r.json()["collection_id"]
    assert r.json()["schedule"]["interval_hours"] == 12

    r = c.get("/api/collections", params={"project_id": "p1"})
    assert r.status_code == 200
    assert len(r.json()["collections"]) == 1

    r = c.get(f"/api/collections/{cid}", params={"project_id": "p1"})
    assert r.status_code == 200
    assert r.json()["name"] == "VIANT"

    r = c.patch(
        f"/api/collections/{cid}",
        params={"project_id": "p1"},
        json={"name": "VIANT v2"},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "VIANT v2"

    r = c.post(f"/api/collections/{cid}/refresh", params={"project_id": "p1"})
    assert r.status_code == 200
    snap = r.json()
    assert snap["total"] == 0 and snap["added"] == []

    r = c.delete(f"/api/collections/{cid}", params={"project_id": "p1"})
    assert r.status_code == 200 and r.json()["deleted"] is True

    r = c.get(f"/api/collections/{cid}", params={"project_id": "p1"})
    assert r.status_code == 404


def test_api_validation_and_404(data_dir):
    c = _client()
    r = c.post("/api/collections", json={"project_id": "p1", "name": "n"})
    assert r.status_code == 422  # query missing
    r = c.post(
        "/api/collections/refresh",
        params={"project_id": "p1"},
    )
    assert r.status_code in (404, 405)
    r = c.post("/api/collections/col_missing/refresh", params={"project_id": "p1"})
    assert r.status_code == 404

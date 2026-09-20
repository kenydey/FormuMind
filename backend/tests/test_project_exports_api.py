"""Dim-4 project export file shelf API tests."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db.database import make_engine
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test_exports.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("FORMUMIND_DB_URL", db_url)
    import app.db.database as db_mod
    import app.db.project_store as ps_mod
    import app.config as cfg

    db_mod._default.clear()
    ps_mod._store = None
    cfg.get_settings.cache_clear()
    make_engine(db_url)
    return TestClient(app)


def _create(client: TestClient) -> str:
    r = client.post("/api/projects", json={"title": "导出货架测试"})
    assert r.status_code == 200
    return r.json()["id"]


def test_export_roundtrip(client):
    pid = _create(client)
    r = client.post(
        f"/api/projects/{pid}/exports",
        json={"filename": "leaderboard.csv", "content": "a,b\n1,2\n"},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "leaderboard.csv"
    assert r.json()["size"] > 0

    listed = client.get(f"/api/projects/{pid}/exports").json()
    assert any(x["name"] == "leaderboard.csv" for x in listed)

    dl = client.get(f"/api/projects/{pid}/exports/leaderboard.csv")
    assert dl.status_code == 200
    assert b"a,b" in dl.content

    assert client.delete(f"/api/projects/{pid}/exports/leaderboard.csv").status_code == 200
    assert client.get(f"/api/projects/{pid}/exports").json() == []


def test_export_rejects_path_traversal(client):
    pid = _create(client)
    r = client.post(
        f"/api/projects/{pid}/exports",
        json={"filename": "../evil.csv", "content": "x"},
    )
    assert r.status_code == 400


def test_export_unknown_project(client):
    r = client.get("/api/projects/no-such-project/exports")
    assert r.status_code == 404


def test_export_upload_multipart(client):
    pid = _create(client)
    r = client.post(
        f"/api/projects/{pid}/exports/upload",
        files={"file": ("note.txt", b"hello shelf", "text/plain")},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "note.txt"
    assert client.get(f"/api/projects/{pid}/exports/note.txt").content == b"hello shelf"


def test_delete_project_purges_exports(client):
    pid = _create(client)
    client.post(
        f"/api/projects/{pid}/exports",
        json={"filename": "keep-me.json", "content": "{}"},
    )
    deleted = client.delete(f"/api/projects/{pid}")
    assert deleted.status_code == 200
    assert deleted.json().get("exports_removed", 0) >= 1

"""W3-8: memories API router — list (scope/scope_id/q filters + pagination) and delete by id.

The router is mounted directly on a bare FastAPI app here (it is NOT
registered in main.py by design); the session factory is monkeypatched to a
tmp sqlite DB.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import memories as memories_api
from app.db.database import Base, make_engine, make_session_factory
from app.services import agent_memory


@pytest.fixture()
def sf(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/memories_api_test.db")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture()
def client(sf, monkeypatch):
    monkeypatch.setattr(memories_api, "_session_factory", lambda: sf)
    app = FastAPI()
    app.include_router(memories_api.router)
    return TestClient(app)


def _seed(sf):
    agent_memory.remember("global", None, "gk", "全局记忆", session_factory=sf)
    agent_memory.remember("project", "p1", "pk1", "p1 偏好水性", session_factory=sf)
    agent_memory.remember("project", "p1", "pk2", "p1 偏好高固含", session_factory=sf)
    agent_memory.remember("project", "p2", "pk3", "p2 的记录", session_factory=sf)
    agent_memory.remember("user", "u1", "uk", "u1 的记录", session_factory=sf)


def test_list_empty(client):
    r = client.get("/api/memories")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == [] and body["total"] == 0
    assert body["page"] == 1 and body["page_size"] == 20


def test_list_all_and_fields(client, sf):
    _seed(sf)
    body = client.get("/api/memories").json()
    assert body["total"] == 5
    keys = {i["key"] for i in body["items"]}
    assert keys == {"gk", "pk1", "pk2", "pk3", "uk"}
    item = next(i for i in body["items"] if i["key"] == "pk1")
    assert item["scope"] == "project" and item["scope_id"] == "p1"
    assert item["value"] == "p1 偏好水性"
    assert item["created_at"] and item["updated_at"]


def test_list_scope_filter(client, sf):
    _seed(sf)
    body = client.get("/api/memories", params={"scope": "project"}).json()
    assert body["total"] == 3
    assert all(i["scope"] == "project" for i in body["items"])


def test_list_scope_id_filter(client, sf):
    _seed(sf)
    body = client.get("/api/memories", params={"scope": "project", "scope_id": "p1"}).json()
    assert body["total"] == 2
    assert all(i["scope_id"] == "p1" for i in body["items"])


def test_list_q_filter(client, sf):
    _seed(sf)
    body = client.get("/api/memories", params={"q": "水性"}).json()
    assert body["total"] == 1
    assert body["items"][0]["key"] == "pk1"


def test_list_q_like_special_chars(client, sf):
    # LIKE wildcards in q must be treated literally, not as patterns.
    agent_memory.remember("global", None, "pct", "100% 固含_测试", session_factory=sf)
    body = client.get("/api/memories", params={"q": "100%"}).json()
    assert body["total"] == 1
    assert body["items"][0]["key"] == "pct"


def test_list_pagination(client, sf):
    _seed(sf)
    p1 = client.get("/api/memories", params={"page": 1, "page_size": 2}).json()
    p2 = client.get("/api/memories", params={"page": 2, "page_size": 2}).json()
    p3 = client.get("/api/memories", params={"page": 3, "page_size": 2}).json()
    assert p1["total"] == 5 and len(p1["items"]) == 2
    assert len(p2["items"]) == 2 and len(p3["items"]) == 1
    ids = [i["id"] for i in p1["items"] + p2["items"] + p3["items"]]
    assert len(set(ids)) == 5


def test_list_invalid_scope_400(client):
    r = client.get("/api/memories", params={"scope": "bogus"})
    assert r.status_code == 400


def test_delete_ok(client, sf):
    _seed(sf)
    mid = client.get("/api/memories", params={"q": "pk1"}).json()["items"][0]["id"]
    r = client.delete(f"/api/memories/{mid}")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "id": mid}
    assert client.get("/api/memories").json()["total"] == 4
    # deleted row no longer recallable via FTS either
    hits = agent_memory.recall("水性", scope="project", scope_id="p1", session_factory=sf)
    assert all(h["key"] != "pk1" for h in hits)


def test_delete_missing_404(client):
    r = client.delete("/api/memories/99999")
    assert r.status_code == 404

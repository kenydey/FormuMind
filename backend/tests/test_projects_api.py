"""Tests for /api/projects CRUD."""
from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.db.database import make_engine, make_session_factory
from app.db.project_store import ProjectStore
from app.domain.project_workspace import ProjectWorkspace, default_requirement
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test_projects.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("FORMUMIND_DB_URL", db_url)
    # Reset cached engine/store
    import app.db.database as db_mod
    import app.db.project_store as ps_mod

    db_mod._default.clear()
    ps_mod._store = None
    make_engine(db_url)
    return TestClient(app)


def test_create_and_get_project(client):
    r = client.post("/api/projects", json={"title": "水性防腐涂料"})
    assert r.status_code == 200
    body = r.json()
    pid = body["id"]
    assert body["title"]
    assert body["workspace"]["requirement"]["domain"] == "anticorrosion_coating"

    r2 = client.get(f"/api/projects/{pid}")
    assert r2.status_code == 200
    assert r2.json()["workspace"]["search_query"] == "水性防腐涂料"


def test_list_projects(client):
    client.post("/api/projects", json={"title": "A"})
    client.post("/api/projects", json={"title": "B"})
    r = client.get("/api/projects")
    assert r.status_code == 200
    assert len(r.json()) >= 2


def test_update_project(client):
    r = client.post("/api/projects", json={})
    pid = r.json()["id"]
    ws = r.json()["workspace"]
    ws["search_query"] = "updated topic"
    ws["sources"] = [
        {
            "source": "local",
            "identifier": "doc#1",
            "title": "Test doc",
            "snippet": "Some content about epoxy coatings",
            "relevance": 1.0,
        }
    ]
    r2 = client.put(f"/api/projects/{pid}", json={"workspace": ws})
    assert r2.status_code == 200
    assert r2.json()["workspace"]["search_query"] == "updated topic"
    assert len(r2.json()["workspace"]["sources"]) == 1

    listed = client.get("/api/projects").json()
    match = next(x for x in listed if x["id"] == pid)
    assert match["source_count"] == 1


def test_project_workspace_persists_adaptive_doe(client):
    r = client.post("/api/projects", json={})
    pid = r.json()["id"]
    ws = r.json()["workspace"]
    ws["adaptive_doe"] = {
        "strategy_label": "exploration",
        "strategy_rationale": "已完成 2 次实验",
        "run_explanations": [
            {
                "run_id": 1,
                "strategy": "exploration",
                "summary": "探索低 Zn 区域",
                "nearest_experiment_ids": [],
            }
        ],
        "anomalies": [],
        "recommended_next_action": "继续执行推荐批次",
        "budget_remaining": 10,
    }
    assert client.put(f"/api/projects/{pid}", json={"workspace": ws}).status_code == 200
    loaded = client.get(f"/api/projects/{pid}").json()["workspace"]
    assert loaded["adaptive_doe"]["strategy_label"] == "exploration"
    assert loaded["adaptive_doe"]["run_explanations"][0]["summary"] == "探索低 Zn 区域"
    assert loaded["adaptive_doe"]["budget_remaining"] == 10


def test_delete_project(client):
    pid = client.post("/api/projects", json={}).json()["id"]
    assert client.delete(f"/api/projects/{pid}").status_code == 200
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_migrate_local(client):
    snap = {
        "id": "legacy-1",
        "timestamp": "2026-01-01T00:00:00Z",
        "domain": "degreaser",
        "headline": "脱脂剂 · carbon_steel",
        "requirement": {"domain": "degreaser", "substrate": "carbon_steel", "cleaning_efficiency": 90},
        "leaderboard": [],
        "models": [],
        "optimization_history": [1.0, 2.0],
    }
    r = client.post("/api/projects/migrate-local", json={"snapshots": [snap]})
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["has_optimize"] is True


def test_project_store_unit(tmp_path):
    db_url = f"sqlite:///{(tmp_path / 'unit.db').as_posix()}"
    engine = make_engine(db_url)
    store = ProjectStore(make_session_factory(engine))
    detail = store.create(title="Unit test")
    assert detail.id
    updated = store.update(
        detail.id,
        ProjectWorkspace(search_query="hello", requirement=default_requirement()),
    )
    assert updated is not None
    assert updated.workspace.search_query == "hello"

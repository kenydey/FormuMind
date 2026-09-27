"""Tests for W3-9 wiring: GET /api/session-plans/pending (approval center feed)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import session_plans as plans_api
from app.services import session_plan as svc


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_data_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture()
def api_client(data_dir):
    app = FastAPI()
    app.include_router(plans_api.router)
    return TestClient(app)


def _phases():
    return [
        {"name": "design", "steps": ["define factors", "choose design"]},
        {"name": "execute", "steps": ["run batch 1"]},
    ]


def _submit(api_client, session_id="sess-1"):
    r = api_client.post("/api/session-plans", json={"session_id": session_id, "phases": _phases()})
    assert r.status_code == 200, r.text
    return r.json()


def test_pending_lists_submitted_plan(api_client):
    plan = _submit(api_client)
    r = api_client.get("/api/session-plans/pending")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["plan_id"] == plan["plan_id"]
    assert item["session_id"] == "sess-1"
    assert item["phase_names"] == ["design", "execute"]
    assert item["step_count"] == 3


def test_pending_excludes_decided_plan(api_client):
    plan = _submit(api_client)
    r = api_client.post(f"/api/session-plans/{plan['plan_id']}/decide", json={"approved": True})
    assert r.status_code == 200, r.text
    r = api_client.get("/api/session-plans/pending")
    assert r.status_code == 200, r.text
    assert r.json() == {"items": [], "total": 0}


def test_pending_route_not_shadowed_by_plan_id(api_client):
    # "/pending" must hit the listing, not be treated as plan_id="pending".
    r = api_client.get("/api/session-plans/pending")
    assert r.status_code == 200, r.text
    assert "items" in r.json()


def test_pending_skips_corrupt_file(data_dir, api_client):
    _submit(api_client)
    bad = data_dir / "session_plans" / "corrupt.json"
    bad.write_text("{not valid json", encoding="utf-8")
    r = api_client.get("/api/session-plans/pending")
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1  # corrupt file skipped, fail-open

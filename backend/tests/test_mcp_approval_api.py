"""Tests for the MCP approval REST API (W3-11)."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.mcp_approvals import router
from app.services import mcp_approval


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "mcp_approval.db"
    monkeypatch.setattr(mcp_approval, "_default_db_path", lambda: db)
    # ensure_store caches per path? force fresh by calling ensure_store via module funcs
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _seed_pending(monkeypatch, tmp_path):
    import time

    db = tmp_path / "mcp_approval.db"
    mcp_approval.ensure_store(db)
    # default policy is ask -> evaluate_call creates a pending request;
    # requested_at uses the monotonic clock, so seed with a fresh monotonic now
    res = mcp_approval.evaluate_call(
        "srv1", "tool_a", session_id="s1", path=db, now=time.monotonic()
    )
    assert res["verdict"] == "deny" and res["reason"] == "approval_required"
    return db, res["request_id"]


def test_pending_lists_request(tmp_path, monkeypatch):
    db = tmp_path / "mcp_approval.db"
    monkeypatch.setattr(mcp_approval, "_default_db_path", lambda: db)
    _seed_pending(monkeypatch, tmp_path)
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/mcp/approvals/pending")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["server_id"] == "srv1" and item["tool_name"] == "tool_a"
    assert item["session_id"] == "s1"


def test_decide_allow_persists_policy(tmp_path, monkeypatch):
    db = tmp_path / "mcp_approval.db"
    monkeypatch.setattr(mcp_approval, "_default_db_path", lambda: db)
    _, rid = _seed_pending(monkeypatch, tmp_path)
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post(f"/api/mcp/approvals/{rid}/decide", json={"decision": "allow", "scope": "session"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # second decide on same request -> 404 (not pending anymore)
    r2 = c.post(f"/api/mcp/approvals/{rid}/decide", json={"decision": "deny"})
    assert r2.status_code == 404


def test_decide_missing_request_404(tmp_path, monkeypatch):
    db = tmp_path / "mcp_approval.db"
    monkeypatch.setattr(mcp_approval, "_default_db_path", lambda: db)
    mcp_approval.ensure_store(db)
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/api/mcp/approvals/424242/decide", json={"decision": "deny"})
    assert r.status_code == 404


def test_decide_invalid_payload_422(tmp_path, monkeypatch):
    db = tmp_path / "mcp_approval.db"
    monkeypatch.setattr(mcp_approval, "_default_db_path", lambda: db)
    mcp_approval.ensure_store(db)
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/api/mcp/approvals/1/decide", json={"decision": "maybe", "scope": "once"})
    assert r.status_code == 422
    r = c.post("/api/mcp/approvals/1/decide", json={"decision": "allow", "scope": "forever"})
    assert r.status_code == 422

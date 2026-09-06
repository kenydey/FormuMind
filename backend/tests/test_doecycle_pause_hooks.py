"""Pause / status hooks for DOE cycle must call sync Redis helpers (no await)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_pause_doecycle_sets_flag(monkeypatch):
    calls: list[tuple[int, bool]] = []

    def fake_pause(campaign_id: int, is_paused: bool) -> bool:
        calls.append((campaign_id, is_paused))
        return True

    monkeypatch.setattr("app.services.workbench_loop.pause_resume_doecyle", fake_pause)
    r = client.post("/api/experiments/hooks/pause-doecycle/42", json={"isPaused": True})
    assert r.status_code == 200
    assert r.json()["status"] == "success"
    assert calls == [(42, True)]


def test_doecycle_status_returns_payload(monkeypatch):
    monkeypatch.setattr(
        "app.services.workbench_loop.get_doecyle_status",
        lambda campaign_id: {"isPaused": True, "lastUpdated": "t", "campaignId": campaign_id},
    )
    r = client.get("/api/experiments/hooks/doecyle-status/9")
    assert r.status_code == 200
    body = r.json()
    assert body["isPaused"] is True
    assert body["campaignId"] == 9


def test_doecycle_status_degraded_when_redis_down(monkeypatch):
    monkeypatch.setattr("app.services.workbench_loop.get_doecyle_status", lambda _cid: None)
    r = client.get("/api/experiments/hooks/doecyle-status/3")
    assert r.status_code == 200
    body = r.json()
    assert body["isPaused"] is False
    assert body.get("degraded") is True

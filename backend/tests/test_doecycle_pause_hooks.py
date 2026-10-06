"""Pause / status hooks for DOE cycle must call the sync service helpers (no await).

The behaviour behind them - the database-backed flag, the TTL, the lapse, ownership - is in
``test_doe_cycle_pause.py``; this file pins the seam: what the endpoints hand to the helpers.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _any_campaign(monkeypatch):
    monkeypatch.setattr("app.api.experiments.campaign_owner", lambda campaign_id: None)


def test_pause_doecycle_sets_flag(monkeypatch):
    _any_campaign(monkeypatch)
    calls: list[tuple[int, bool, float | None]] = []

    def fake_pause(campaign_id: int, is_paused: bool, *, ttl_hours: float | None = None) -> bool:
        calls.append((campaign_id, is_paused, ttl_hours))
        return True

    monkeypatch.setattr("app.services.workbench_loop.pause_resume_doecyle", fake_pause)
    monkeypatch.setattr(
        "app.services.workbench_loop.get_doecyle_status",
        lambda campaign_id: {"isPaused": True, "pausedUntil": "2026-10-03T09:00:00Z"},
    )
    r = client.post("/api/experiments/hooks/pause-doecycle/42", json={"isPaused": True})
    assert r.status_code == 200
    assert r.json()["status"] == "success"
    assert r.json()["pausedUntil"] == "2026-10-03T09:00:00Z"
    r = client.post("/api/experiments/hooks/pause-doecycle/42", json={"isPaused": True, "ttlHours": 3})
    assert r.status_code == 200
    r = client.post("/api/experiments/hooks/pause-doecycle/42", json={"isPaused": False})
    assert r.status_code == 200
    assert calls == [(42, True, None), (42, True, 3.0), (42, False, None)]


def test_doecycle_status_returns_payload(monkeypatch):
    _any_campaign(monkeypatch)
    monkeypatch.setattr(
        "app.services.workbench_loop.get_doecyle_status",
        lambda campaign_id: {"isPaused": True, "lastUpdated": "t", "campaignId": campaign_id},
    )
    r = client.get("/api/experiments/hooks/doecyle-status/9")
    assert r.status_code == 200
    body = r.json()
    assert body["isPaused"] is True
    assert body["campaignId"] == 9


def test_doecycle_status_degraded_when_the_database_cannot_be_read(monkeypatch):
    _any_campaign(monkeypatch)
    monkeypatch.setattr("app.services.workbench_loop.get_doecyle_status", lambda _cid: None)
    r = client.get("/api/experiments/hooks/doecyle-status/3")
    assert r.status_code == 200
    body = r.json()
    assert body["isPaused"] is False
    assert body.get("degraded") is True

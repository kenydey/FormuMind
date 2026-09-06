"""DOE cycle API: async submit contract for ``POST /api/doe/cycle``."""
from __future__ import annotations

from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _body() -> dict:
    return {
        "requirement": {"domain": "anticorrosion_coating"},
        "workbench_campaign_id": 7,
    }


def test_doe_cycle_accepts_valid_body(monkeypatch):
    captured: dict = {}

    def fake_submit(task, payload, kind, *, outbox_id=None, owner_id=None):
        captured["task"] = task
        captured["payload"] = payload
        captured["kind"] = kind
        captured["outbox_id"] = outbox_id
        return JSONResponse(content={"task_id": "fake", "kind": kind}, status_code=202)

    monkeypatch.setattr("app.api.doe.submit", fake_submit)
    monkeypatch.setattr("app.api.doe.enqueue_outbox", lambda op, payload: "outbox-doe")
    r = client.post("/api/doe/cycle", json=_body())
    assert r.status_code == 202
    assert captured["kind"] == "doe_cycle"
    assert captured["outbox_id"] == "outbox-doe"
    assert captured["payload"]["requirement"]["domain"] == "anticorrosion_coating"
    assert captured["payload"]["workbench_campaign_id"] == 7
    from app.worker.tasks import run_doe_cycle_task

    assert captured["task"] is run_doe_cycle_task


def test_doe_cycle_requires_requirement():
    r = client.post("/api/doe/cycle", json={"workbench_campaign_id": 1})
    assert r.status_code == 422


def test_doe_cycle_broker_down_503(monkeypatch):
    monkeypatch.setattr("app.api._dispatch.broker_reachable", lambda: False)
    monkeypatch.setattr("app.api.doe.enqueue_outbox", lambda op, payload: None)
    r = client.post("/api/doe/cycle", json=_body())
    assert r.status_code == 503
    assert "Redis" in r.text

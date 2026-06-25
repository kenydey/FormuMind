"""CQRS async task API: 202 enqueue + GET SSE stream."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.main import app
from tests.redis_helpers import requires_redis

client = TestClient(app)
_REQUIREMENT = {
    "domain": "anticorrosion_coating",
    "substrate": "carbon_steel",
    "salt_spray_hours": 500,
    "film_weight_gsm": 70,
    "cure_temperature_c": 80,
    "cleaning_efficiency": 90,
    "voc_limit_gpl": 420,
    "ph_target": None,
    "notes": "",
    "objectives": [],
}


def _poll_status(status_url: str, *, timeout_s: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        last = client.get(status_url).json()
        if last.get("state") in ("completed", "failed"):
            return last
        time.sleep(0.05)
    return last


def test_optimize_returns_202_with_stream_url():
    r = client.post("/api/optimize", json={"requirement": _REQUIREMENT, "iterations": 4})
    assert r.status_code == 202
    body = r.json()
    assert body["task_id"]
    assert body["stream_url"] == f"/api/tasks/{body['task_id']}/stream"
    assert body["status_url"] == f"/api/tasks/{body['task_id']}"


def test_deep_research_202_completes_via_status():
    body = {
        "topic": "low-temperature curing anti-corrosion primer",
        "requirement": _REQUIREMENT,
        "sources": [],
        "query": "low-temperature curing anti-corrosion primer",
    }
    r = client.post("/api/research/deep", json=body)
    assert r.status_code == 202
    handle = r.json()
    assert "stream_url" in handle and "status_url" in handle

    st = _poll_status(handle["status_url"], timeout_s=120.0)
    assert st["state"] == "completed", st
    assert st["result"]["report"]["report_markdown"]


@requires_redis
def test_sse_stream_returns_progress_events():
    body = {        "topic": "epoxy zinc phosphate primer",
        "requirement": _REQUIREMENT,
        "sources": [],
        "query": "epoxy zinc phosphate primer",
    }
    r = client.post("/api/research/deep", json=body)
    assert r.status_code == 202
    handle = r.json()

    with client.stream("GET", handle["stream_url"]) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        text = "".join(resp.iter_text())

    assert "data:" in text
    assert "COMPLETED" in text or "RUNNING" in text

    st = _poll_status(handle["status_url"], timeout_s=120.0)
    assert st["state"] == "completed"

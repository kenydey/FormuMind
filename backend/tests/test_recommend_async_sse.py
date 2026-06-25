"""Recommend research async API: 202 enqueue + SSE / status completion."""
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


def _poll_status(status_url: str, *, timeout_s: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        last = client.get(status_url).json()
        if last.get("state") in ("completed", "failed"):
            return last
        time.sleep(0.05)
    return last


def test_recommend_returns_202_with_stream_url():
    body = {**_REQUIREMENT, "sources": [], "query": "epoxy zinc phosphate primer"}
    r = client.post("/api/research/recommend", json=body)
    assert r.status_code == 202
    handle = r.json()
    assert handle["task_id"]
    assert handle["stream_url"] == f"/api/tasks/{handle['task_id']}/stream"
    assert handle["status_url"] == f"/api/tasks/{handle['task_id']}"


def test_recommend_completes_via_status():
    body = {**_REQUIREMENT, "sources": [], "query": "epoxy zinc phosphate primer"}
    r = client.post("/api/research/recommend", json=body)
    assert r.status_code == 202
    handle = r.json()

    st = _poll_status(handle["status_url"], timeout_s=60.0)
    assert st["state"] == "completed", st
    research = st["result"]["research"]
    assert isinstance(research.get("recommended"), list)
    assert research.get("requirement_headline")


@requires_redis
def test_recommend_sse_stream_returns_progress_events():
    body = {**_REQUIREMENT, "sources": [], "query": "epoxy zinc phosphate primer"}
    r = client.post("/api/research/recommend", json=body)
    assert r.status_code == 202
    handle = r.json()

    with client.stream("GET", handle["stream_url"]) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        text = "".join(resp.iter_text())

    assert "data:" in text
    assert "COMPLETED" in text or "RUNNING" in text

    st = _poll_status(handle["status_url"], timeout_s=60.0)
    assert st["state"] == "completed"
    assert st["result"]["research"]["recommended"] is not None

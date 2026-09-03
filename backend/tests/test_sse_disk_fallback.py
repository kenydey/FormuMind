"""SSE stream works without Redis via disk / file progress fallback."""
from __future__ import annotations

import json
import time
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.worker.task_progress import (
    TaskProgressStatus,
    get_task_meta,
    publish_progress,
)

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


def test_publish_progress_file_fallback_without_redis(monkeypatch):
    """When Redis is down, progress meta is written to disk."""
    monkeypatch.setattr(
        "app.worker.task_progress._redis_client",
        lambda: (_ for _ in ()).throw(ConnectionError("redis down")),
    )
    task_id = f"file-fallback-{uuid.uuid4().hex}"
    publish_progress(
        task_id,
        TaskProgressStatus.RUNNING,
        stage="retrieve",
        message="正在检索",
        progress=0.3,
        kind="recommend",
    )
    meta = get_task_meta(task_id)
    assert meta is not None
    assert meta["status"] == "RUNNING"
    assert meta["stage"] == "retrieve"
    event = json.loads(meta["last_event"])
    assert event["message"] == "正在检索"


def test_recommend_sse_completes_without_redis():
    """Eager recommend + disk terminal should yield COMPLETED on SSE without Redis."""
    body = {**_REQUIREMENT, "sources": [], "query": "epoxy zinc phosphate primer"}
    r = client.post("/api/research/recommend", json=body)
    assert r.status_code == 202
    handle = r.json()

    with client.stream("GET", handle["stream_url"]) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        text = "".join(resp.iter_text())

    assert "COMPLETED" in text
    assert "data:" in text

    st = client.get(handle["status_url"]).json()
    assert st["state"] == "completed"
    assert st["result"]["research"]["recommended"] is not None

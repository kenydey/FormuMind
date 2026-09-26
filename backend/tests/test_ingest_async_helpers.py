"""Helpers for async file-ingest (202 + task poll) assertions."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient


def poll_task(client: TestClient, task_id: str, *, timeout_s: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout_s
    body: dict = {}
    while time.monotonic() < deadline:
        s = client.get(f"/api/tasks/{task_id}")
        assert s.status_code == 200, s.text
        body = s.json()
        if body.get("state") in ("completed", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} not terminal after {timeout_s}s: {body}")

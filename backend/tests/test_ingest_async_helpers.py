"""Helpers for async file-ingest (202 + task poll) assertions."""
from __future__ import annotations

import faulthandler
import sys
import tempfile
import time

from fastapi.testclient import TestClient


def thread_stacks() -> str:
    """Every thread's stack right now - the one thing a timeout on a CI runner cannot otherwise tell us.

    A task that did not end in 30 s on Windows once left only ``state: pending`` in the log; with the stacks the next one
    says whether the worker was blocked (and on what) or had already finished.
    """
    with tempfile.TemporaryFile("w+") as fh:
        faulthandler.dump_traceback(file=fh, all_threads=True)
        fh.seek(0)
        return fh.read()


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
    print(f"--- thread stacks when task {task_id} was still {body.get('state')!r} after {timeout_s}s ---\n{thread_stacks()}", file=sys.stderr)
    raise AssertionError(f"task {task_id} not terminal after {timeout_s}s: {body}")

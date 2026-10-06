"""Registering a task twice must not move it backwards (round-5; a flake CI caught on a Windows runner).

``POST /api/ingest`` calls ``dispatch_file_ingest`` (which registers the task as ``queued`` and starts the worker) and then
``accepted_response`` (which registers it again to build the 202). A job that ends in milliseconds - a missing parser, a
duplicate upload - has finished by the time the second registration runs, and it rewrote ``queued`` over the progress meta
and the snapshot: the upload sat at "queued" for good although its result was stored. ``test_upload_with_no_parser_is_a_422``
saw exactly that body (``state: pending, message: queued, result: {error: ...}``) after a 30 s poll.

The race is a few milliseconds wide, so these tests place the second registration where the race puts it instead of hoping
to hit it.
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.domain.schemas import TaskState
from app.main import app
from app.services import parsing
from app.worker import task_progress, tasks
from app.worker.task_progress import TaskProgressStatus, get_task_meta, publish_progress
from app.worker.tasks import task_manager


@pytest.fixture(autouse=True)
def _task_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_TASK_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("FORMUMIND_TASK_PROGRESS_DIR", str(tmp_path / "tasks" / "progress"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _status(task_id: str) -> str:
    return (get_task_meta(task_id) or {}).get("status", "")


def test_the_first_registration_announces_queued():
    task_manager.register_celery_task("reg-first", "loop")
    meta = get_task_meta("reg-first")
    assert meta["status"] == "PENDING" and meta["message"] == "queued" and meta["kind"] == "loop"
    assert task_manager.get("reg-first").state == TaskState.pending


def test_a_second_registration_leaves_a_running_task_running():
    task_manager.register_celery_task("reg-running", "loop")
    publish_progress("reg-running", TaskProgressStatus.RUNNING, stage="retrieve", message="searching", progress=0.4, kind="loop")
    task_manager.register_celery_task("reg-running", "loop")
    meta = get_task_meta("reg-running")
    assert meta["status"] == "RUNNING" and meta["stage"] == "retrieve" and meta["message"] == "searching"
    assert task_manager.get("reg-running").progress == pytest.approx(0.4)


def test_a_second_registration_leaves_a_finished_task_finished_even_if_the_snapshot_cannot_be_read(monkeypatch):
    """The terminal snapshot is what the old guard looked at. Unreadable (Windows, mid-replace) or not yet written
    (the worker is between its two writes), it let the registration through."""
    task_manager.register_celery_task("reg-done", "file_ingest")
    task_progress.persist_result("reg-done", {"error": "boom"}, failed=True)
    tasks._persist_terminal("reg-done", "file_ingest", {"error": "boom"}, failed=True, message="boom")
    real = tasks.load_persisted_task
    monkeypatch.setattr(tasks, "load_persisted_task", lambda tid: None)
    task_manager.register_celery_task("reg-done", "file_ingest")
    monkeypatch.setattr(tasks, "load_persisted_task", real)
    assert _status("reg-done") == "FAILED"
    status = task_manager.get("reg-done")
    assert status.state == TaskState.failed and status.result == {"error": "boom"}


def test_a_late_first_registration_does_not_regress_a_task_that_already_ran():
    """Celery path: the id comes from ``.delay()``, so the registration can only come after the worker may have started."""
    publish_progress("reg-late", TaskProgressStatus.RUNNING, stage="ingest", message="parsing", kind="file_ingest")
    task_manager.register_celery_task("reg-late", "file_ingest")
    assert _status("reg-late") == "RUNNING"


def test_a_second_registration_still_records_the_owner():
    task_manager.register_celery_task("reg-owner", "loop")
    task_manager.register_celery_task("reg-owner", "loop", owner_id="alice")
    assert task_manager._owners["reg-owner"] == "alice"
    assert task_manager.get("reg-owner").owner_id == "alice"


def test_a_task_the_registration_has_not_seen_is_still_registered_in_a_fresh_directory(tmp_path, monkeypatch):
    """The decision rests on the durable progress meta, not on this process's memory: a reused id in a clean directory
    (every test, a restarted server) is a new task."""
    task_manager.register_celery_task("reg-reused", "loop")
    monkeypatch.setenv("FORMUMIND_TASK_DIR", str(tmp_path / "other"))
    monkeypatch.setenv("FORMUMIND_TASK_PROGRESS_DIR", str(tmp_path / "other" / "progress"))
    assert _status("reg-reused") == ""
    task_manager.register_celery_task("reg-reused", "loop")
    assert _status("reg-reused") == "PENDING"


def test_the_upload_route_cannot_leave_a_task_that_ended_at_once_stuck_at_queued(monkeypatch):
    """The failing sequence, end to end: the worker thread finishes before the route's second registration, which also
    finds the terminal snapshot unreadable."""
    from app.services import ingestion

    def explode(*_args, **_kwargs):
        raise parsing.ParserUnavailable("pdf", "未安装任何 PDF 解析器")

    monkeypatch.setattr(ingestion, "ingest_files_batch", explode)
    worker_done = threading.Event()
    real_safe = tasks._safe_file_ingest

    def safe_then_signal(task_id, payload):
        try:
            real_safe(task_id, payload)
        finally:
            worker_done.set()

    monkeypatch.setattr(tasks, "_safe_file_ingest", safe_then_signal)

    real_register = tasks.TaskManager.register_celery_task
    real_load = tasks.load_persisted_task
    calls: list[str] = []

    def second_registration_after_the_worker(self, task_id, kind, owner_id=None):
        calls.append(task_id)
        if len(calls) == 1:  # dispatch_file_ingest's own registration, before the thread starts
            return real_register(self, task_id, kind, owner_id)
        assert worker_done.wait(30), "the worker never finished"
        monkeypatch.setattr(tasks, "load_persisted_task", lambda tid: None)  # the terminal snapshot cannot be read
        try:
            return real_register(self, task_id, kind, owner_id)
        finally:
            monkeypatch.setattr(tasks, "load_persisted_task", real_load)

    monkeypatch.setattr(tasks.TaskManager, "register_celery_task", second_registration_after_the_worker)
    client = TestClient(app)
    resp = client.post("/api/ingest", files={"file": ("spec.pdf", b"%PDF-1.4", "application/pdf")})
    assert resp.status_code == 202, resp.text
    task_id = resp.json()["task_id"]
    assert len(calls) == 2 and set(calls) == {task_id}, "the route registers a second time - that is what this guards"

    deadline = time.monotonic() + 10
    body: dict = {}
    while time.monotonic() < deadline:
        body = client.get(f"/api/tasks/{task_id}").json()
        if body["state"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert body["state"] == "failed", body
    assert "PDF" in body["result"]["error"]

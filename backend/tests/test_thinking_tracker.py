"""Dim-2 ThinkingTracker unit tests (disk fallback — no Redis required)."""

from __future__ import annotations

import uuid

import pytest

from app.worker.task_progress import (
    ThinkingTracker,
    TaskProgressStatus,
    attach_thinking,
    get_task_meta,
    thinking_step,
)


@pytest.fixture(autouse=True)
def _isolate_progress_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_TASK_PROGRESS_DIR", str(tmp_path / "progress"))
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    # Force Redis path to fail so disk fallback is exercised deterministically.
    monkeypatch.setattr(
        "app.worker.task_progress._redis_client",
        lambda: (_ for _ in ()).throw(RuntimeError("redis down")),
    )


def test_thinking_step_shape():
    s = thinking_step("retrieve", "正在检索", kind="stage", detail="kb", status="running")
    assert s["id"] == "retrieve"
    assert s["kind"] == "stage"
    assert s["status"] == "running"


def test_attach_thinking_merges():
    data = attach_thinking({"foo": 1}, [thinking_step("a", "A")])
    assert data["foo"] == 1
    assert data["thinking"][0]["id"] == "a"


def test_tracker_emits_snapshot_and_upserts():
    task_id = f"think-{uuid.uuid4().hex}"
    tracker = ThinkingTracker(task_id, kind="recommend")
    ev1 = tracker.emit("retrieve", "正在检索", progress=0.2, step_id="retrieve", title="检索")
    assert ev1.status == TaskProgressStatus.RUNNING
    assert ev1.data and len(ev1.data["thinking"]) == 1
    assert ev1.data["thinking"][0]["status"] == "running"

    ev2 = tracker.emit("grade", "评估", progress=0.5, step_id="grade", title="评估")
    steps = ev2.data["thinking"]
    assert len(steps) == 2
    assert steps[0]["status"] == "done"
    assert steps[1]["id"] == "grade"
    assert steps[1]["status"] == "running"

    # Same stage upsert — still 2 steps
    ev3 = tracker.emit("grade", "评估中…", progress=0.55, step_id="grade", title="评估中")
    assert len(ev3.data["thinking"]) == 2
    assert ev3.data["thinking"][1]["title"] == "评估中"

    tracker.finish()
    assert all(s["status"] == "done" for s in tracker.steps)

    meta = get_task_meta(task_id)
    assert meta is not None
    assert meta["stage"] == "grade"

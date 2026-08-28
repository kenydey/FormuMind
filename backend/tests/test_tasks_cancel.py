"""P2 异步可观测与取消：cancel 终态与 elapsed/stage 透传."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.worker.task_progress import TaskProgressStatus, publish_progress, get_task_meta
from app.worker.tasks import task_manager


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_TASK_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("FORMUMIND_TASK_PROGRESS_DIR", str(tmp_path / "tasks" / "progress"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_cancel_pending_task_reaches_cancelled(monkeypatch):
    # 用 register_celery_task 创建 pending 任务（不依赖真实 celery）
    task_id = "test-cancel-1"
    task_manager.register_celery_task(task_id, "loop")
    # 模拟运行中
    publish_progress(task_id, TaskProgressStatus.RUNNING, stage="retrieve", message="正在检索", progress=0.2, kind="loop")
    time.sleep(0.05)
    client = TestClient(app)
    r = client.post(f"/api/tasks/{task_id}/cancel")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "cancelled"
    assert body["stage"] == "cancelled"
    # GET 应保持 cancelled 且带 stage
    r2 = client.get(f"/api/tasks/{task_id}")
    assert r2.status_code == 200
    assert r2.json()["state"] == "cancelled"
    # SSE meta 应为 CANCELLED
    meta = get_task_meta(task_id)
    assert meta and meta.get("status") == "CANCELLED"
    # 二次 cancel 幂等：已 cancelled 不再 revoke
    r3 = client.post(f"/api/tasks/{task_id}/cancel")
    assert r3.status_code == 200
    assert r3.json()["state"] == "cancelled"


def test_get_task_exposes_stage_and_elapsed(monkeypatch):
    task_id = "test-elapsed-1"
    task_manager.register_celery_task(task_id, "loop")
    publish_progress(task_id, TaskProgressStatus.RUNNING, stage="generate", message="生成中", progress=0.5, kind="loop")
    time.sleep(0.05)
    client = TestClient(app)
    r = client.get(f"/api/tasks/{task_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["stage"] == "generate"
    assert body["elapsed_ms"] is not None
    assert body["elapsed_ms"] >= 0
    # completed 不应可 cancel
    publish_progress(task_id, TaskProgressStatus.COMPLETED, message="done", progress=1.0, kind="loop")
    # 手动 persist completed
    from app.worker.tasks import _persist_terminal

    _persist_terminal(task_id, "loop", {"ok": 1})
    r2 = client.post(f"/api/tasks/{task_id}/cancel")
    assert r2.status_code == 200
    assert r2.json()["state"] == "completed"


def test_sse_terminal_includes_cancelled(monkeypatch):
    task_id = "test-sse-cancelled"
    task_manager.register_celery_task(task_id, "loop")
    publish_progress(task_id, TaskProgressStatus.CANCELLED, stage="cancelled", message="已取消", kind="loop")
    from app.worker.tasks import _persist_task
    from app.domain.schemas import TaskState, TaskStatus

    _persist_task(task_id, TaskStatus(task_id=task_id, kind="loop", state=TaskState.cancelled, message="已取消", stream_url=f"/api/tasks/{task_id}/stream", stage="cancelled"))
    from app.api.tasks import _terminal_event_from_disk

    ev = _terminal_event_from_disk(task_id)
    assert ev is not None
    assert ev.status == TaskProgressStatus.CANCELLED
    assert ev.stage == "cancelled"

"""v3C 任务 owner 隔离：alice 的任务 bob 不可见（403），公共任务放行。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.worker.task_progress import TaskProgressStatus, publish_progress
from app.worker.tasks import task_manager


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_MULTI_USER", "false")
    monkeypatch.setenv("FORMUMIND_TASK_DIR", str(tmp_path / "tasks_c"))
    monkeypatch.setenv("FORMUMIND_TASK_PROGRESS_DIR", str(tmp_path / "tasks_c" / "progress"))
    get_settings.cache_clear()
    task_manager._kinds.clear()
    task_manager._owners.clear()
    yield
    get_settings.cache_clear()


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_public_task_accessible_by_anyone(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_MULTI_USER", "true")
    monkeypatch.setenv("FORMUMIND_API_TOKENS_JSON", '{"alice":"tok_alice","bob":"tok_bob"}')
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "true")
    get_settings.cache_clear()

    task_id = "public-task-1"
    task_manager.register_celery_task(task_id, "loop", owner_id=None)
    publish_progress(task_id, TaskProgressStatus.RUNNING, stage="retrieve", message="x", kind="loop")

    client = TestClient(app)
    for tok in ("tok_alice", "tok_bob"):
        r = client.get(f"/api/tasks/{task_id}", headers=_auth_header(tok))
        assert r.status_code == 200, r.text


def test_owner_task_forbidden_for_other_user(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_MULTI_USER", "true")
    monkeypatch.setenv("FORMUMIND_API_TOKENS_JSON", '{"alice":"tok_alice","bob":"tok_bob"}')
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "true")
    get_settings.cache_clear()

    task_id = "alice-task-1"
    task_manager.register_celery_task(task_id, "recommend", owner_id="alice")
    publish_progress(task_id, TaskProgressStatus.RUNNING, stage="retrieve", message="y", kind="recommend")

    client = TestClient(app)
    # alice 可访问
    r = client.get(f"/api/tasks/{task_id}", headers=_auth_header("tok_alice"))
    assert r.status_code == 200
    # bob 越权 403
    r2 = client.get(f"/api/tasks/{task_id}", headers=_auth_header("tok_bob"))
    assert r2.status_code == 403, r2.text
    # cancel 越权同样 403
    r3 = client.post(f"/api/tasks/{task_id}/cancel", headers=_auth_header("tok_bob"))
    assert r3.status_code == 403, r3.text
    # alice 可 cancel
    r4 = client.post(f"/api/tasks/{task_id}/cancel", headers=_auth_header("tok_alice"))
    assert r4.status_code == 200
    assert r4.json()["state"] == "cancelled"


def test_api_creates_task_with_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_MULTI_USER", "true")
    monkeypatch.setenv("FORMUMIND_API_TOKENS_JSON", '{"alice":"tok_alice","bob":"tok_bob"}')
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "true")
    # Keep celery_eager=True (suite default): this test asserts owner isolation on
    # the created task handle, not broker connectivity. Forcing eager=False with
    # no Redis on CI yields 503 before an owner can be recorded.
    get_settings.cache_clear()

    client = TestClient(app)
    # 触发一个真实异步任务（recommend）并验证 owner
    from app.domain.schemas import Requirement, ProductDomain, ObjectiveSpec

    req = Requirement(domain=ProductDomain.anticorrosion_coating, objectives=[ObjectiveSpec(metric="salt_spray_hours", weight=1.0, direction="maximize")])
    payload = {**req.model_dump(), "sources": [], "query": "test"}
    r = client.post("/api/research/recommend", json=payload, headers=_auth_header("tok_alice"))
    assert r.status_code == 202, r.text
    tid = r.json()["task_id"]
    # alice 能查
    assert client.get(f"/api/tasks/{tid}", headers=_auth_header("tok_alice")).status_code == 200
    # bob 403
    r2 = client.get(f"/api/tasks/{tid}", headers=_auth_header("tok_bob"))
    assert r2.status_code == 403

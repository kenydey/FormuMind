"""Optional-dependency status + install endpoints (offline, no real pip runs)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services import dependencies as deps

client = TestClient(app)


def test_status_lists_catalog_with_required_fields():
    rows = deps.status()
    assert rows, "catalog should not be empty"
    keys = {"pip_name", "import_name", "extra", "enables", "installed", "version"}
    for row in rows:
        assert keys <= set(row)
        assert isinstance(row["installed"], bool)
        # version is set iff installed
        assert (row["version"] is None) or row["installed"]


def test_validate_names_rejects_unknown():
    deps.validate_names(["arxiv", "ddgs"])  # known → no raise
    try:
        deps.validate_names(["arxiv", "totally-not-a-real-pkg"])
    except ValueError as exc:
        assert "totally-not-a-real-pkg" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for unknown dependency")


def test_install_empty_returns_failure_without_running_pip():
    result = deps.install([])
    assert result["ok"] is False
    assert "未选择" in result["summary"]


def test_get_dependencies_endpoint():
    r = client.get("/api/dependencies")
    assert r.status_code == 200
    body = r.json()
    assert "dependencies" in body and isinstance(body["dependencies"], list)
    assert "online_core_missing" in body
    names = {d["pip_name"] for d in body["dependencies"]}
    assert {"anthropic", "arxiv", "ddgs", "molbloom"} <= names


def test_install_endpoint_rejects_empty_selection():
    r = client.post("/api/dependencies/install", json={"names": []})
    assert r.status_code == 400


def test_install_endpoint_rejects_unknown_name():
    r = client.post("/api/dependencies/install", json={"names": ["evil-pkg-xyz"]})
    assert r.status_code == 400


def test_install_endpoint_accepts_known_name_non_eager(monkeypatch):
    """Non-eager path still publishes via ``.delay()`` and returns its task id."""
    class _FakeAsyncResult:
        id = "fake-task-id"

    from app.api import _dispatch
    from app.config import get_settings
    from app.worker import tasks as worker_tasks

    settings = get_settings()
    monkeypatch.setattr(settings, "celery_eager", False, raising=False)
    monkeypatch.setattr(_dispatch, "broker_reachable", lambda: True)
    monkeypatch.setattr(
        worker_tasks.run_deps_install_task, "delay", lambda payload: _FakeAsyncResult()
    )
    r = client.post("/api/dependencies/install", json={"names": ["arxiv"]})
    assert r.status_code == 202
    body = r.json()
    assert body["task_id"] == "fake-task-id"
    assert body["stream_url"].endswith("fake-task-id/stream")
    assert body["status_url"].endswith("fake-task-id")


def test_install_endpoint_accepts_known_name_eager(monkeypatch):
    """Eager path returns 202 immediately with a fresh task_id (delay is unused)."""
    import uuid

    from app.worker import tasks as worker_tasks

    # Avoid running a real pip install in the eager background thread.
    monkeypatch.setattr(
        worker_tasks.run_deps_install_task,
        "apply",
        lambda *a, **k: None,
    )
    r = client.post("/api/dependencies/install", json={"names": ["arxiv"]})
    assert r.status_code == 202
    body = r.json()
    task_id = body["task_id"]
    uuid.UUID(task_id)  # raises if not a UUID
    assert body["stream_url"].endswith(f"{task_id}/stream")
    assert body["status_url"].endswith(task_id)

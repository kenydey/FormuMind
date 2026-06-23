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
    assert {"anthropic", "arxiv", "ddgs", "chemcrow"} <= names


def test_install_endpoint_rejects_empty_selection():
    r = client.post("/api/dependencies/install", json={"names": []})
    assert r.status_code == 400


def test_install_endpoint_rejects_unknown_name():
    r = client.post("/api/dependencies/install", json={"names": ["evil-pkg-xyz"]})
    assert r.status_code == 400


def test_install_endpoint_accepts_known_name(monkeypatch):
    # Don't actually run pip: stub the background submitter.
    from app.worker.tasks import task_manager

    monkeypatch.setattr(
        task_manager, "submit_dependency_install", lambda names, upgrade=False: "fake-task-id"
    )
    r = client.post("/api/dependencies/install", json={"names": ["arxiv"]})
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == "fake-task-id"
    assert body["poll_url"].endswith("fake-task-id")

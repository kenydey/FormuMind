"""FORMUMIND_DEPS_INSTALL_ENABLED gate for POST /api/dependencies/install."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_deps_install_disabled_by_default_in_production(monkeypatch):
    monkeypatch.delenv("FORMUMIND_DEPS_INSTALL_ENABLED", raising=False)
    monkeypatch.setenv("FORMUMIND_ENVIRONMENT", "production")
    get_settings.cache_clear()
    assert get_settings().deps_install_enabled is False


def test_deps_install_enabled_by_default_in_development(monkeypatch):
    monkeypatch.delenv("FORMUMIND_DEPS_INSTALL_ENABLED", raising=False)
    monkeypatch.setenv("FORMUMIND_ENVIRONMENT", "development")
    get_settings.cache_clear()
    assert get_settings().deps_install_enabled is True


def test_install_returns_403_when_disabled(monkeypatch):
    monkeypatch.setenv("FORMUMIND_DEPS_INSTALL_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_ENVIRONMENT", "development")
    get_settings.cache_clear()
    with TestClient(app) as client:
        r = client.post("/api/dependencies/install", json={"names": ["optuna"]})
    assert r.status_code == 403
    assert "DEPS_INSTALL" in r.json()["detail"]


def test_list_dependencies_reports_install_enabled(monkeypatch):
    monkeypatch.setenv("FORMUMIND_DEPS_INSTALL_ENABLED", "false")
    get_settings.cache_clear()
    with TestClient(app) as client:
        r = client.get("/api/dependencies")
    assert r.status_code == 200
    assert r.json()["install_enabled"] is False


def test_install_accepted_when_enabled(monkeypatch):
    monkeypatch.setenv("FORMUMIND_DEPS_INSTALL_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_ENVIRONMENT", "production")
    get_settings.cache_clear()

    def fake_submit(task, payload, kind):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=202, content={"task_id": "deps-test-1", "kind": kind})

    monkeypatch.setattr("app.api.dependencies.submit", fake_submit)
    monkeypatch.setattr("app.api.dependencies.deps.validate_names", lambda names: None)

    with TestClient(app) as client:
        r = client.post(
            "/api/dependencies/install",
            json={"names": ["optuna"], "upgrade": False},
        )
    assert r.status_code == 202
    assert r.json()["task_id"] == "deps-test-1"

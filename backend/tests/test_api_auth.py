"""API bearer token middleware tests."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.middleware.api_auth import reset_dev_token_cache


@pytest.fixture
def auth_client(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_API_TOKEN", "test-secret-token")
    monkeypatch.setenv("FORMUMIND_ENVIRONMENT", "test")
    get_settings.cache_clear()
    reset_dev_token_cache()
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()
    reset_dev_token_cache()


def test_health_is_public_without_token(auth_client):
    r = auth_client.get("/health")
    assert r.status_code == 200


def test_api_requires_bearer_token(auth_client):
    r = auth_client.get("/api/meta")
    assert r.status_code == 401


def test_api_accepts_valid_bearer_token(auth_client):
    r = auth_client.get(
        "/api/meta",
        headers={"Authorization": "Bearer test-secret-token"},
    )
    assert r.status_code == 200


def test_api_rejects_wrong_token(auth_client):
    r = auth_client.get(
        "/api/meta",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert r.status_code == 401


def test_auth_status_is_public_without_token(auth_client):
    r = auth_client.get("/api/auth/status")
    assert r.status_code == 200
    body = r.json()
    assert body["auth_required"] is True


def test_api_auth_disabled_by_default_in_development(monkeypatch):
    monkeypatch.delenv("FORMUMIND_API_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("FORMUMIND_ENVIRONMENT", "development")
    get_settings.cache_clear()
    try:
        assert get_settings().api_auth_enabled is False
    finally:
        get_settings.cache_clear()


def test_api_auth_enabled_by_default_in_production(monkeypatch):
    monkeypatch.delenv("FORMUMIND_API_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("FORMUMIND_ENVIRONMENT", "production")
    get_settings.cache_clear()
    try:
        assert get_settings().api_auth_enabled is True
    finally:
        get_settings.cache_clear()


def test_startup_fails_fast_when_production_auth_has_no_token(monkeypatch):
    """Prod + auth enabled + no token must abort startup, not 500 on every request."""
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "true")
    monkeypatch.delenv("FORMUMIND_API_TOKEN", raising=False)
    monkeypatch.setenv("FORMUMIND_ENVIRONMENT", "production")
    get_settings.cache_clear()
    reset_dev_token_cache()
    try:
        with pytest.raises(RuntimeError, match="FORMUMIND_API_TOKEN"):
            with TestClient(app):
                pass
    finally:
        get_settings.cache_clear()
        reset_dev_token_cache()


def test_requests_get_503_when_auth_misconfigured_at_runtime(monkeypatch):
    """If settings become misconfigured after startup, return a clear 503 (not 500)."""
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_ENVIRONMENT", "test")
    get_settings.cache_clear()
    reset_dev_token_cache()
    try:
        with TestClient(app) as client:
            monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "true")
            monkeypatch.delenv("FORMUMIND_API_TOKEN", raising=False)
            monkeypatch.setenv("FORMUMIND_ENVIRONMENT", "production")
            get_settings.cache_clear()
            r = client.get("/api/meta")
            assert r.status_code == 503
            assert "FORMUMIND_API_TOKEN" in r.json()["detail"]
    finally:
        get_settings.cache_clear()
        reset_dev_token_cache()

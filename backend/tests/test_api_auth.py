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
    settings = get_settings()
    assert settings.api_auth_enabled is True
    assert settings.api_token == "test-secret-token"
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


def test_empty_dev_token_file_is_regenerated(monkeypatch, tmp_path):
    """Empty data/.api_token must not disable auth (was a silent bypass)."""
    from app.middleware import api_auth

    token_path = tmp_path / ".api_token"
    token_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(api_auth, "_TOKEN_PATH", token_path)
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "true")
    monkeypatch.delenv("FORMUMIND_API_TOKEN", raising=False)
    monkeypatch.setenv("FORMUMIND_ENVIRONMENT", "development")
    get_settings.cache_clear()
    reset_dev_token_cache()
    try:
        settings = get_settings()
        tok = api_auth.resolve_api_token(settings)
        assert tok is not None and len(tok) >= 16
        assert token_path.read_text(encoding="utf-8").strip() == tok
    finally:
        get_settings.cache_clear()
        reset_dev_token_cache()


def test_auth_enabled_with_no_resolvable_token_returns_401(monkeypatch):
    from app.middleware import api_auth

    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_API_TOKEN", "configured-but-ignored")
    monkeypatch.setenv("FORMUMIND_ENVIRONMENT", "test")
    get_settings.cache_clear()
    reset_dev_token_cache()

    def _none(_settings):
        return None

    monkeypatch.setattr(api_auth, "resolve_api_token", _none)
    try:
        with TestClient(app) as client:
            r = client.get("/api/meta")
        assert r.status_code == 401
        assert "no token" in r.json()["detail"].lower()
    finally:
        get_settings.cache_clear()
        reset_dev_token_cache()

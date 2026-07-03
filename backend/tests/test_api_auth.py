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

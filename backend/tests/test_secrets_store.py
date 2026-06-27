"""Tests for secrets_store — .env persistence isolated via FORMUMIND_ENV_FILE."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.secrets_store import (
    list_secret_status,
    read_env_file,
    resolve_env_path,
    probe_secret,
    update_secrets,
    write_env_updates,
)

client = TestClient(app)


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.setenv("FORMUMIND_ENV_FILE", str(env_file))
    get_settings.cache_clear()
    yield env_file
    get_settings.cache_clear()


def test_resolve_env_path_honors_override(isolated_env):
    assert resolve_env_path() == isolated_env


def test_write_and_read_env_roundtrip(isolated_env):
    write_env_updates({"FORMUMIND_SERPAPI_API_KEY": "serp-test-key"}, isolated_env)
    data = read_env_file(isolated_env)
    assert data["FORMUMIND_SERPAPI_API_KEY"] == "serp-test-key"


def test_update_secrets_persists_and_masks(isolated_env):
    update_secrets({"serpapi_api_key": "abcdefghijklmnop"})
    data = read_env_file(isolated_env)
    assert data["FORMUMIND_SERPAPI_API_KEY"] == "abcdefghijklmnop"

    items = {s["id"]: s for s in list_secret_status()}
    assert items["serpapi_api_key"]["set"] is True
    assert "abcd" in items["serpapi_api_key"]["masked"]
    assert "mnop" in items["serpapi_api_key"]["masked"]


def test_secrets_api_get_and_post(isolated_env):
    r = client.get("/api/settings/secrets")
    assert r.status_code == 200
    assert any(s["id"] == "tavily_api_key" for s in r.json()["secrets"])

    r2 = client.post(
        "/api/settings/secrets",
        json={"updates": {"tavily_api_key": "tvly-test-12345"}},
    )
    assert r2.status_code == 200
    assert "tavily_api_key" in r2.json()["updated"]
    tavily = next(s for s in r2.json()["secrets"] if s["id"] == "tavily_api_key")
    assert tavily["set"] is True


def test_probe_secret_epo_requires_both_keys(isolated_env):
    update_secrets({"epo_consumer_key": "key-only"})
    out = probe_secret("epo_consumer_key")
    assert out["ok"] is False
    assert "同时" in out["message"]


def test_llm_settings_api_key_writes_env(isolated_env, monkeypatch):
    from app.services import llm as llm_mod

    monkeypatch.setattr(
        llm_mod,
        "test_connection",
        lambda: {"ok": True, "provider": "deepseek", "model": "deepseek-v4-pro", "message": "ok"},
    )
    client.post(
        "/api/settings",
        json={"provider": "deepseek", "model": "deepseek-v4-pro", "api_key": "sk-from-settings"},
    )
    data = read_env_file(isolated_env)
    assert data["FORMUMIND_DEEPSEEK_API_KEY"] == "sk-from-settings"

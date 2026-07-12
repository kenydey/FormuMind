"""Tests — runtime-configurable boolean env flags (Settings UI)."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.services import env_flags


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    # Redirect .env persistence to a throwaway file.
    monkeypatch.setenv("FORMUMIND_ENV_FILE", str(tmp_path / ".env"))
    # update_env_flags mutates os.environ directly (that is its job) — snapshot
    # every flag key so tests cannot leak state into the rest of the suite.
    flag_keys = [f.env_key for f in env_flags.FLAG_REGISTRY]
    before = {k: os.environ.get(k) for k in flag_keys}
    get_settings.cache_clear()
    yield
    for k, v in before.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()


def _client() -> TestClient:
    from app.main import app

    return TestClient(app)


# ── registry sanity ──────────────────────────────────────────────────────────


def test_registry_only_contains_boolean_settings_fields():
    for flag in env_flags.FLAG_REGISTRY:
        field = Settings.model_fields[flag.attr]
        assert isinstance(field.default, bool), flag.attr
        assert flag.env_key == f"FORMUMIND_{flag.attr.upper()}"
        assert flag.label and flag.description
        assert flag.category in env_flags.CATEGORY_LABELS


def test_registry_excludes_selflockout_and_environment():
    attrs = {f.attr for f in env_flags.FLAG_REGISTRY}
    assert "api_auth_enabled" not in attrs
    assert "environment" not in attrs


def test_list_env_flags_reports_effective_and_default(monkeypatch):
    monkeypatch.setenv("FORMUMIND_FULLTEXT_ENRICH", "true")  # default false
    get_settings.cache_clear()
    flags = {f["attr"]: f for f in env_flags.list_env_flags()}
    assert flags["fulltext_enrich"]["value"] is True
    assert flags["fulltext_enrich"]["default"] is False
    assert flags["kb_v2_enabled"]["value"] is True
    assert flags["kb_v2_enabled"]["default"] is True


# ── update semantics ─────────────────────────────────────────────────────────


def test_update_applies_immediately_and_persists(monkeypatch, tmp_path):
    assert get_settings().content_filter_llm_judge is False

    updated, rejected = env_flags.update_env_flags({"content_filter_llm_judge": True})
    assert updated == ["content_filter_llm_judge"]
    assert rejected == []
    # Live process env + settings cache both updated.
    assert os.environ["FORMUMIND_CONTENT_FILTER_LLM_JUDGE"] == "true"
    assert get_settings().content_filter_llm_judge is True
    # Persisted to .env for restart survival.
    env_text = (tmp_path / ".env").read_text()
    assert "FORMUMIND_CONTENT_FILTER_LLM_JUDGE=true" in env_text

    env_flags.update_env_flags({"content_filter_llm_judge": False})
    assert get_settings().content_filter_llm_judge is False
    assert "FORMUMIND_CONTENT_FILTER_LLM_JUDGE=false" in (tmp_path / ".env").read_text()


def test_update_rejects_unknown_and_nonregistry_attrs():
    updated, rejected = env_flags.update_env_flags(
        {"api_auth_enabled": False, "no_such_flag": True}
    )
    assert updated == []
    assert set(rejected) == {"api_auth_enabled", "no_such_flag"}
    assert "FORMUMIND_NO_SUCH_FLAG" not in os.environ


def test_update_preserves_llm_runtime_overlay(monkeypatch):
    from app.services.runtime_secrets import effective_setting, get_runtime_secrets

    get_runtime_secrets().set("llm_provider", "deepseek")
    env_flags.update_env_flags({"auto_retrain": False})
    assert effective_setting(get_settings(), "llm_provider") == "deepseek"
    get_runtime_secrets().clear()


def test_update_survives_readonly_env_file(monkeypatch):
    def boom(updates, path=None):
        raise OSError("read-only fs")

    monkeypatch.setattr("app.services.secrets_store.write_env_updates", boom)
    updated, _ = env_flags.update_env_flags({"openalex_enabled": False})
    assert updated == ["openalex_enabled"]
    assert get_settings().openalex_enabled is False  # live change still applied


# ── API endpoints ────────────────────────────────────────────────────────────


def test_get_env_flags_endpoint():
    resp = _client().get("/api/settings/env-flags")
    assert resp.status_code == 200
    flags = resp.json()["flags"]
    assert len(flags) == len(env_flags.FLAG_REGISTRY)
    sample = next(f for f in flags if f["attr"] == "kb_v2_enabled")
    assert sample["env_key"] == "FORMUMIND_KB_V2_ENABLED"
    assert isinstance(sample["value"], bool)
    assert sample["category_label"]


def test_post_env_flags_endpoint_roundtrip(tmp_path):
    client = _client()
    resp = client.post(
        "/api/settings/env-flags",
        json={"updates": {"fulltext_enrich": True, "bogus": True}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["updated"] == ["fulltext_enrich"]
    assert data["rejected"] == ["bogus"]
    flags = {f["attr"]: f for f in data["flags"]}
    assert flags["fulltext_enrich"]["value"] is True
    assert get_settings().fulltext_enrich is True

    # Restore default.
    client.post("/api/settings/env-flags", json={"updates": {"fulltext_enrich": False}})
    assert get_settings().fulltext_enrich is False


def test_post_env_flags_type_validation():
    resp = _client().post(
        "/api/settings/env-flags", json={"updates": {"kb_v2_enabled": "yes"}}
    )
    assert resp.status_code == 422  # pydantic enforces strict booleans

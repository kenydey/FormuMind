"""LLM settings — remote model list refresh."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services import llm as llm_mod

client = TestClient(app)


def test_refresh_models_openai_compatible(monkeypatch):
    monkeypatch.setattr(
        llm_mod,
        "fetch_openai_compatible_model_ids",
        lambda base_url, api_key, timeout=30.0: [
            "gpt-4o",
            "gpt-4o-mini",
        ],
    )
    monkeypatch.setattr(
        "app.services.llm.get_settings",
        lambda: type(
            "S",
            (),
            {
                "get_active_api_key": lambda self: "sk-test",
                "llm_timeout_seconds": 30.0,
            },
        )(),
    )

    r = client.post(
        "/api/settings/models/refresh",
        json={"provider": "openai", "baseUrl": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["source"] == "remote"
    ids = {m["id"] for m in body["models"]}
    assert "gpt-4o" in ids
    assert "gpt-4o-mini" in ids
    assert all("embedding" not in m for m in ids)


def test_refresh_models_without_key_falls_back_static():
    r = client.post(
        "/api/settings/models/refresh",
        json={"provider": "anthropic", "model": "claude-sonnet-4-6"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "static"
    assert any(m["id"] == "claude-sonnet-4-6" for m in body["models"])


def test_is_listable_chat_model_filters_embeddings():
    assert llm_mod._is_listable_chat_model("gpt-4o") is True
    assert llm_mod._is_listable_chat_model("text-embedding-3-small") is False

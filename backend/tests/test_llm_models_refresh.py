"""LLM settings — remote model list refresh."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services import llm as llm_mod

client = TestClient(app)


def test_refresh_models_openai_compatible(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_mod, "_MODEL_CACHE_REL", tmp_path / "cache.json")
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


# ── 远端模型列表本地缓存 ─────────────────────────────────────────────────────


def test_model_cache_persist_and_override(tmp_path, monkeypatch):
    """「更新列表」持久化的远端列表会覆盖硬编码目录，且不影响其他供应商。"""
    monkeypatch.setattr(llm_mod, "_MODEL_CACHE_REL", tmp_path / "cache.json")

    # 无缓存：static_models_for_provider 返回硬编码目录
    assert {m["id"] for m in llm_mod.static_models_for_provider("deepseek")} == {
        "deepseek-v4-pro",
        "deepseek-v4-flash",
        "deepseek-v4-flash-vision-exp",
    }

    # 持久化远端列表
    llm_mod._persist_provider_models(
        "deepseek",
        [
            {"id": "deepseek-v4-flash-vision-exp", "label": "DeepSeek Vision"},
            {"id": "deepseek-v4-pro", "label": "DeepSeek Pro"},
        ],
    )

    # static_models_for_provider 读缓存（远端覆盖硬编码）
    assert [m["id"] for m in llm_mod.static_models_for_provider("deepseek")] == [
        "deepseek-v4-flash-vision-exp",
        "deepseek-v4-pro",
    ]

    # providers_with_cache 只覆盖 deepseek，其他供应商保持硬编码
    deepseek = next(p for p in llm_mod.providers_with_cache() if p["id"] == "deepseek")
    assert [m["id"] for m in deepseek["models"]] == [
        "deepseek-v4-flash-vision-exp",
        "deepseek-v4-pro",
    ]
    openai = next(p for p in llm_mod.providers_with_cache() if p["id"] == "openai")
    assert "gpt-4o" in {m["id"] for m in openai["models"]}


def test_remote_refresh_persists_cache(tmp_path, monkeypatch):
    """refresh 成功后把 merge 后的远端列表写入缓存文件。"""
    monkeypatch.setattr(llm_mod, "_MODEL_CACHE_REL", tmp_path / "cache.json")
    monkeypatch.setattr(
        llm_mod,
        "fetch_openai_compatible_model_ids",
        lambda base_url, api_key, timeout=30.0: ["gpt-4o", "gpt-4o-mini"],
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
    assert r.json()["source"] == "remote"

    cache = llm_mod._load_model_cache()
    assert "openai" in cache
    assert {m["id"] for m in cache["openai"]} == {"gpt-4o", "gpt-4o-mini"}

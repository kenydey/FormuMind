"""The vision role over HTTP: read, update, persist, probe.

The probe endpoint carries the weight here. `vision_available()` deliberately
refuses to claim a model can read a picture — for a rented endpoint that is
unknowable — so this is the only thing in the system that can answer "does it
actually work", and it must never report success on a call that did not happen.
"""
from __future__ import annotations

import sys
import types

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.runtime_secrets import get_runtime_secrets
from app.services.secrets_store import apply_persisted_ui_settings, reload_settings

client = TestClient(app)

_ENV_KEYS = (
    "FORMUMIND_LLM_PROVIDER",
    "FORMUMIND_LLM_MODEL",
    "FORMUMIND_LLM_BASE_URL",
    "FORMUMIND_VISION_PROVIDER",
    "FORMUMIND_VISION_MODEL",
    "FORMUMIND_VISION_BASE_URL",
    "FORMUMIND_VISION_API_KEY",
    "FORMUMIND_CUSTOM_API_KEY",
    "FORMUMIND_CUSTOM_BASE_URL",
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("FORMUMIND_ENV_FILE", str(env_file))
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    rs = get_runtime_secrets()
    rs.clear()
    get_settings.cache_clear()
    yield
    rs.clear()
    get_settings.cache_clear()


def _fake_openai(monkeypatch, *, reply: str = "OK", raises: Exception | None = None):
    mod = types.ModuleType("openai")
    calls: list[dict] = []

    class OpenAI:
        def __init__(self, **kwargs):
            calls.append(kwargs)

            def create(**kw):
                calls[-1]["messages"] = kw.get("messages")
                if raises is not None:
                    raise raises
                msg = types.SimpleNamespace(content=reply)
                return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=create)
            )

    mod.OpenAI = OpenAI
    monkeypatch.setitem(sys.modules, "openai", mod)
    return calls


# ── GET /api/settings ────────────────────────────────────────────────────────


def test_settings_exposes_the_vision_section():
    body = client.get("/api/settings").json()
    assert "vision" in body
    v = body["vision"]
    assert set(v) >= {"provider", "model", "base_url", "key_set", "inherits", "configured"}


def test_vision_reports_inheriting_by_default():
    get_runtime_secrets().set("llm_provider", "openai")
    get_runtime_secrets().set("llm_model", "gpt-4o")
    get_runtime_secrets().set("openai_api_key", "sk-text")

    v = client.get("/api/settings").json()["vision"]
    assert v["inherits"] is True
    assert v["provider"] == ""       # the UI renders "follow the text model"
    assert v["model"] == "gpt-4o"


# ── POST /api/settings/vision ────────────────────────────────────────────────


def test_updating_vision_does_not_touch_the_text_role():
    get_runtime_secrets().set("llm_provider", "deepseek")
    get_runtime_secrets().set("llm_model", "deepseek-v4-pro")
    get_runtime_secrets().set("deepseek_api_key", "sk-text")

    r = client.post(
        "/api/settings/vision",
        json={"provider": "openai", "model": "gpt-4o", "api_key": "sk-vision"},
    )
    assert r.status_code == 200
    v = r.json()
    assert v["provider"] == "openai" and v["model"] == "gpt-4o"
    assert v["inherits"] is False

    body = client.get("/api/settings").json()
    assert body["provider"] == "deepseek", "the text role must be untouched"
    assert body["model"] == "deepseek-v4-pro"


def test_clearing_the_provider_returns_to_inheriting():
    get_runtime_secrets().set("llm_provider", "openai")
    get_runtime_secrets().set("llm_model", "gpt-4o")
    get_runtime_secrets().set("openai_api_key", "sk-text")
    client.post("/api/settings/vision", json={"provider": "anthropic", "model": "claude"})
    assert client.get("/api/settings").json()["vision"]["inherits"] is False

    client.post("/api/settings/vision", json={"provider": "", "model": ""})
    v = client.get("/api/settings").json()["vision"]
    assert v["inherits"] is True
    assert v["model"] == "gpt-4o"


def test_custom_endpoint_url_gets_v1_appended():
    r = client.post(
        "/api/settings/vision",
        json={
            "provider": "custom",
            "model": "tgi",
            "api_key": "hf_token",
            # The URL HuggingFace shows you, without the OpenAI-compatible route.
            "baseUrl": "https://vlm.endpoints.huggingface.cloud",
        },
    )
    assert r.status_code == 200
    assert r.json()["base_url"] == "https://vlm.endpoints.huggingface.cloud/v1"


def test_a_hostile_base_url_is_rejected():
    r = client.post(
        "/api/settings/vision",
        json={"provider": "custom", "model": "tgi", "baseUrl": "http://169.254.169.254/v1"},
    )
    assert r.status_code == 422


def test_vision_settings_survive_a_reload(monkeypatch, tmp_path):
    """vision_model used to be env-only, so anything chosen outside .env was gone
    after a restart. It now persists like the text role."""
    client.post(
        "/api/settings/vision",
        json={
            "provider": "custom",
            "model": "Qwen/Qwen2.5-VL-7B-Instruct",
            "api_key": "hf_token",
            "baseUrl": "https://vlm.endpoints.huggingface.cloud/v1",
        },
    )
    # Drop the in-process overlay and rebuild Settings from the persisted .env,
    # which is what a restart does.
    get_runtime_secrets().clear()
    reload_settings()
    apply_persisted_ui_settings()

    v = client.get("/api/settings").json()["vision"]
    assert v["provider"] == "custom"
    assert v["model"] == "Qwen/Qwen2.5-VL-7B-Instruct"
    assert v["base_url"] == "https://vlm.endpoints.huggingface.cloud/v1"


# ── POST /api/settings/vision/test ───────────────────────────────────────────


def _configure_custom():
    rs = get_runtime_secrets()
    rs.set("vision_provider", "custom")
    rs.set("vision_model", "tgi")
    rs.set("vision_api_key", "hf_token")
    rs.set("vision_base_url", "https://vlm.endpoints.huggingface.cloud/v1")


def test_probe_sends_a_real_image_to_the_vision_endpoint(monkeypatch):
    _configure_custom()
    calls = _fake_openai(monkeypatch, reply="OK")

    body = client.post("/api/settings/vision/test").json()
    assert body["ok"] is True
    assert body["provider"] == "custom" and body["model"] == "tgi"
    assert calls[0]["base_url"] == "https://vlm.endpoints.huggingface.cloud/v1"
    assert calls[0]["api_key"] == "hf_token"
    # A real image part, not a text-only ping — a text round-trip proves nothing
    # about whether the model can see.
    parts = calls[0]["messages"][0]["content"]
    kinds = {p["type"] for p in parts}
    assert kinds == {"text", "image_url"}
    url = next(p["image_url"]["url"] for p in parts if p["type"] == "image_url")
    assert url.startswith("data:image/png;base64,")


def test_probe_reports_a_cold_endpoint_as_such(monkeypatch):
    _configure_custom()
    exc = Exception("Service Unavailable")
    exc.response = types.SimpleNamespace(status_code=503)
    _fake_openai(monkeypatch, raises=exc)

    body = client.post("/api/settings/vision/test").json()
    assert body["ok"] is False
    assert "冷启动" in body["message"]


def test_probe_surfaces_a_bad_key(monkeypatch):
    _configure_custom()
    exc = Exception("Invalid credentials")
    exc.response = types.SimpleNamespace(status_code=401)
    _fake_openai(monkeypatch, raises=exc)

    body = client.post("/api/settings/vision/test").json()
    assert body["ok"] is False
    assert "Invalid credentials" in body["message"]


def test_probe_does_not_claim_success_on_an_empty_reply(monkeypatch):
    """Reaching the endpoint is not the same as it being able to see. An empty
    completion must not read as a pass."""
    _configure_custom()
    _fake_openai(monkeypatch, reply="")

    body = client.post("/api/settings/vision/test").json()
    assert body["ok"] is False
    assert "未返回文本" in body["message"]


def test_probe_reports_unconfigured_without_calling_out(monkeypatch):
    calls = _fake_openai(monkeypatch)
    body = client.post("/api/settings/vision/test").json()
    assert body["ok"] is False
    assert body["message"]
    assert calls == [], "nothing to call when no key is configured"


# ── /health/detailed ─────────────────────────────────────────────────────────


def test_health_detailed_reports_the_vision_role():
    """vision_available() was unreachable over HTTP, so a degraded figure gave
    the operator nothing to ask the server about."""
    rs = get_runtime_secrets()
    rs.set("llm_provider", "deepseek")
    rs.set("deepseek_api_key", "sk-text")
    rs.set("vision_provider", "custom")
    rs.set("vision_model", "tgi")
    rs.set("vision_api_key", "hf_token")
    rs.set("vision_base_url", "https://vlm.endpoints.huggingface.cloud/v1")

    body = client.get("/health/detailed").json()
    assert body["vision"]["provider"] == "custom"
    assert body["vision"]["model"] == "tgi"
    assert body["vision"]["inherits"] is False


def test_health_detailed_follows_a_provider_switch():
    """It read cfg.llm_provider directly, so after switching provider in the UI
    this reported the compiled default — useless for the one question it is
    asked, "which model am I actually talking to"."""
    rs = get_runtime_secrets()
    rs.set("llm_provider", "moonshot")
    rs.set("moonshot_api_key", "sk-switched")

    assert client.get("/health/detailed").json()["llm"] == "moonshot"


# ── DeepSeek 视觉模型支持 ────────────────────────────────────────────────────


def test_deepseek_vision_model_is_accepted():
    """deepseek + 视觉模型（deepseek-v4-flash-vision-exp）→ vision_available 可用。"""
    from app.services.vision_extract import vision_available

    rs = get_runtime_secrets()
    rs.set("vision_provider", "deepseek")
    rs.set("vision_model", "deepseek-v4-flash-vision-exp")
    rs.set("deepseek_api_key", "sk-test")
    try:
        ok, hint = vision_available()
        assert ok, hint
    finally:
        rs.clear()


def test_deepseek_text_model_is_rejected_with_vision_hint():
    """deepseek + 文本模型 → 提示指定 deepseek 视觉模型。"""
    from app.services.vision_extract import vision_available

    rs = get_runtime_secrets()
    rs.set("vision_provider", "deepseek")
    rs.set("vision_model", "deepseek-v4-pro")
    rs.set("deepseek_api_key", "sk-test")
    try:
        ok, hint = vision_available()
        assert not ok
        assert "deepseek-v4-flash-vision-exp" in hint
    finally:
        rs.clear()

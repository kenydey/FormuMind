"""GET/POST /api/settings — Runtime LLM + API secrets configuration."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..config import get_settings
from ..services.llm import _provider_default_base_url, list_remote_models, providers_with_cache, test_connection
from ..services.llm_roles import CUSTOM_PROVIDER, VISION, normalize_base_url, resolve_role
from ..services.vision_extract import probe_vision, vision_available
from ..services.runtime_secrets import effective_setting, get_runtime_secrets
from ..services.secrets_store import (
    SECRET_REGISTRY,
    list_secret_status,
    persist_llm_runtime,
    probe_secret,
    update_secrets,
)

router = APIRouter()

# Known LLM provider hostnames (and their API subdomains). A user-supplied
# base_url whose host matches one of these is always accepted; otherwise the
# URL must at least pass the SSRF safety check (_is_safe_url).
_KNOWN_LLM_HOSTS: frozenset[str] = frozenset({
    "anthropic.com",
    "api.anthropic.com",
    "openai.com",
    "api.openai.com",
    "googleapis.com",
    "generativelanguage.googleapis.com",
    "x.ai",
    "api.x.ai",
    "groq.com",
    "api.groq.com",
    "deepseek.com",
    "api.deepseek.com",
    "dashscope.aliyuncs.com",
    "moonshot.cn",
    "api.moonshot.cn",
    "api.minimax.chat",
    # HuggingFace Inference Endpoints. The suffix match below covers every
    # `<name>.<region>.endpoints.huggingface.cloud`, so one entry is enough.
    "huggingface.cloud",
})


def _validate_base_url(url: str | None) -> None:
    """Reject unsafe / non-LLM base_urls that could exfiltrate API keys.

    Accepts None (no override). Otherwise the host must either be a known LLM
    provider domain or at least pass the SSRF safety check.
    """
    if not url:
        return
    from urllib.parse import urlparse

    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=422, detail="base_url must use http(s) scheme")
    host = (parsed.hostname or "").lower()
    if not host:
        raise HTTPException(status_code=422, detail="base_url is missing a hostname")
    if any(host == h or host.endswith(f".{h}") for h in _KNOWN_LLM_HOSTS):
        return
    from ..services.ingestion import _is_safe_url

    if not _is_safe_url(url):
        raise HTTPException(
            status_code=422,
            detail="base_url hostname is not a recognised LLM provider and is not a safe public address",
        )


class LLMSettingsUpdate(BaseModel):
    """Accept both snake_case and camelCase from the frontend."""

    model_config = ConfigDict(populate_by_name=True)

    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = Field(default=None, alias="baseUrl")


class VisionSettingsUpdate(BaseModel):
    """The vision role. An empty ``provider`` means "follow the text model"."""

    model_config = ConfigDict(populate_by_name=True)

    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = Field(default=None, alias="baseUrl")


class LLMModelsRefreshRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider: str | None = None
    base_url: str | None = Field(default=None, alias="baseUrl")
    model: str | None = None


class SecretsUpdate(BaseModel):
    updates: dict[str, str | None] = Field(default_factory=dict)


class EnvFlagsUpdate(BaseModel):
    # StrictBool: only JSON true/false — "yes"/"1" strings are rejected with 422.
    updates: dict[str, StrictBool] = Field(default_factory=dict)


class SecretTestRequest(BaseModel):
    id: str


_SECRET_ATTR_IDS = {item[0] for item in SECRET_REGISTRY}


def _apply_llm_update(update: LLMSettingsUpdate) -> None:
    s = get_settings()
    rs = get_runtime_secrets()
    current_provider = effective_setting(s, "llm_provider")
    # Fill in /v1 for a custom endpoint before validating and storing, so the
    # overlay holds the URL we would actually call — a bare HF endpoint host
    # would otherwise 404 later for no visible reason. Scoped to the custom
    # provider: the catalog URLs are already right, and DeepSeek serves from the
    # domain root, so normalizing it would break a working provider.
    if update.base_url and str(update.provider or current_provider) == CUSTOM_PROVIDER:
        update = update.model_copy(
            update={"base_url": normalize_base_url(update.base_url) or ""}
        )
    _validate_base_url(update.base_url)
    provider_changed = update.provider is not None and update.provider != current_provider
    if update.provider is not None:
        rs.set("llm_provider", update.provider)
    if update.model is not None:
        rs.set("llm_model", update.model)
    if provider_changed and update.base_url is None:
        new_provider = update.provider or current_provider
        rs.set("llm_base_url", _provider_default_base_url(str(new_provider)))
    if update.base_url is not None:
        rs.set("llm_base_url", update.base_url.strip() or _provider_default_base_url(
            str(update.provider or current_provider)
        ))

    effective_provider = str(update.provider or current_provider)
    if effective_provider == CUSTOM_PROVIDER and update.base_url:
        # Mirror it: the catalog has no default to restore a custom endpoint
        # from, so switching to another provider and back would otherwise lose
        # the URL entirely.
        rs.set("custom_base_url", update.base_url.strip())

    if update.api_key is not None:
        key_field = f"{effective_provider}_api_key"
        if key_field in _SECRET_ATTR_IDS:
            update_secrets({key_field: update.api_key})
        elif effective_provider == "anthropic":
            update_secrets({"anthropic_api_key": update.api_key})

    # Overlay changes apply immediately; persist them so a restart reloads
    # the same provider / model / base URL instead of the compiled defaults.
    persist_llm_runtime()


def _apply_vision_update(update: VisionSettingsUpdate) -> None:
    """The vision role, same shape as the text one with two differences.

    An empty provider is meaningful — it means "follow the text model" — and the
    key always goes to ``vision_api_key`` rather than a per-provider secret,
    because a key typed into the vision section is the vision key. The read path
    still falls back to ``{provider}_api_key`` when it is blank, so pointing
    vision at a provider you already have a key for needs no retyping.
    """
    s = get_settings()
    rs = get_runtime_secrets()
    current_provider = effective_setting(s, "vision_provider")
    if update.base_url and str(update.provider or current_provider) == CUSTOM_PROVIDER:
        update = update.model_copy(
            update={"base_url": normalize_base_url(update.base_url) or ""}
        )
    _validate_base_url(update.base_url)

    provider_changed = update.provider is not None and update.provider != current_provider
    if update.provider is not None:
        rs.set("vision_provider", update.provider.strip())
    if update.model is not None:
        rs.set("vision_model", update.model.strip())
    if provider_changed and update.base_url is None:
        rs.set("vision_base_url", _provider_default_base_url(str(update.provider or "")))
    if update.base_url is not None:
        rs.set("vision_base_url", update.base_url.strip() or None)
    if update.api_key is not None:
        update_secrets({"vision_api_key": update.api_key})

    persist_llm_runtime()


def _vision_state() -> dict:
    """What the settings page needs to render the vision section."""
    cfg = resolve_role(VISION)
    configured, hint = vision_available()
    return {
        # Empty provider == inheriting; the UI shows "follow the text model".
        "provider": effective_setting(get_settings(), "vision_provider") or "",
        "model": cfg.model,
        "base_url": cfg.base_url,
        "key_set": bool(cfg.api_key),
        "inherits": cfg.inherited,
        # "configured", never "can read a picture" — see vision_available().
        "configured": configured,
        "hint": hint,
    }


@router.get("/settings")
def get_llm_settings():
    s = get_settings()
    return {
        "provider": effective_setting(s, "llm_provider"),
        "model": effective_setting(s, "llm_model"),
        "key_set": bool(s.get_active_api_key()),
        "base_url": effective_setting(s, "llm_base_url"),
        "providers": providers_with_cache(),
        "vision": _vision_state(),
    }


@router.post("/settings")
def update_llm_settings(update: LLMSettingsUpdate):
    _apply_llm_update(update)
    return test_connection()


@router.post("/settings/vision")
def update_vision_settings(update: VisionSettingsUpdate):
    _apply_vision_update(update)
    return {"ok": True, **_vision_state()}


@router.post("/settings/vision/test")
def test_vision_connection():
    """Send a real image through the real path.

    This exists because ``vision_available()`` deliberately refuses to guess
    whether the configured model can read a picture — for a rented endpoint we
    cannot know which weights were deployed, and a capability table we cannot
    keep accurate is the lying-probe bug this codebase keeps relearning. So
    instead of guessing, offer a button that finds out.
    """
    return probe_vision()


@router.post("/settings/test")
def test_llm_connection():
    return test_connection()


@router.post("/settings/models/refresh")
def refresh_llm_models(body: LLMModelsRefreshRequest | None = None):
    body = body or LLMModelsRefreshRequest()
    _validate_base_url(body.base_url)
    s = get_settings()
    provider = body.provider or effective_setting(s, "llm_provider")
    current_model = body.model or effective_setting(s, "llm_model")
    return list_remote_models(
        provider,
        base_url=body.base_url,
        current_model=current_model,
    )


@router.get("/settings/secrets")
def get_secrets_status():
    return {"secrets": list_secret_status()}


@router.post("/settings/secrets")
def post_secrets_update(body: SecretsUpdate):
    updated = update_secrets(body.updates)
    return {"updated": updated, "secrets": list_secret_status()}


@router.post("/settings/secrets/test")
def post_secret_test(body: SecretTestRequest):
    return probe_secret(body.id)


@router.get("/settings/env-flags")
def get_env_flags():
    from ..services.env_flags import list_env_flags

    return {"flags": list_env_flags()}


@router.post("/settings/env-flags")
def post_env_flags(body: EnvFlagsUpdate):
    from ..services.env_flags import list_env_flags, update_env_flags

    updated, rejected = update_env_flags(body.updates)
    return {"updated": updated, "rejected": rejected, "flags": list_env_flags()}


# ── Formulation mode selector (non-boolean) ─────────────────────────────

FORMULATION_MODE_CHOICES = [
    {"value": "hybrid", "label": "Hybrid 叠加", "desc": "知识库证据 + LLM 合成（推荐）"},
    {"value": "llm_only", "label": "LLM Only", "desc": "纯 LLM 推荐（快速，离线）"},
    {"value": "kb_only", "label": "KB Only", "desc": "仅知识库检索（LLM 降级/验证时）"},
]


class FormulationModeUpdate(BaseModel):
    mode: str = Field(default="hybrid", pattern="^(hybrid|llm_only|kb_only)$")


@router.get("/settings/formulation-mode")
def get_formulation_mode() -> dict:
    s = get_settings()
    return {"current": s.formulation_mode, "choices": FORMULATION_MODE_CHOICES}


@router.post("/settings/formulation-mode")
def set_formulation_mode(body: FormulationModeUpdate) -> dict:
    import os
    from ..services.secrets_store import write_env_updates

    os.environ["FORMUMIND_FORMULATION_MODE"] = body.mode
    try:
        write_env_updates({"FORMUMIND_FORMULATION_MODE": body.mode})
    except OSError:
        pass  # read-only FS — live process env still applied
    get_settings.cache_clear()
    return {"mode": body.mode, "status": "ok"}


# ── DECIMER mode selector (non-boolean) ────────────────────────────────

DECIMER_MODE_CHOICES = [
    {"value": "auto", "label": "Auto 自动", "desc": "运行时探测：GPU 可用→gpu，否则→cpu（推荐）"},
    {"value": "cpu", "label": "CPU 纯识别", "desc": "tensorflow-cpu + 纯识别，无结构切分（当前 VPS）"},
    {"value": "gpu", "label": "GPU 全流程", "desc": "tensorflow 完整版 + decimer-segmentation 先切分后识别（预留）"},
]


class DecimerModeUpdate(BaseModel):
    mode: str = Field(default="auto", pattern="^(auto|cpu|gpu)$")


@router.get("/settings/decimer")
def get_decimer() -> dict:
    from ..services.decimer_ocr import availability

    s = get_settings()
    return {
        "current": s.decimer_mode,
        "choices": DECIMER_MODE_CHOICES,
        "status": availability(),
    }


@router.post("/settings/decimer")
def set_decimer_mode(body: DecimerModeUpdate) -> dict:
    import os
    from ..services.secrets_store import write_env_updates

    os.environ["FORMUMIND_DECIMER_MODE"] = body.mode
    try:
        write_env_updates({"FORMUMIND_DECIMER_MODE": body.mode})
    except OSError:
        pass  # read-only FS — live process env still applied
    get_settings.cache_clear()
    return {"mode": body.mode, "status": "ok"}


# ── OCSR 多后端 ─────────────────────────────────────────────────────────────
OCSR_BACKEND_CHOICES = [
    {"value": "auto", "label": "Auto 自动",
     "desc": "探测 GPU：有 GPU → DECIMER+segmentation，无 GPU → MolScribe（推荐）"},
    {"value": "molscribe", "label": "MolScribe（无 GPU）",
     "desc": "torch-cpu 轻量识别，内存 ~1.9GB、冷启动 ~35s（当前 VPS 默认）"},
    {"value": "decimer", "label": "DECIMER（有 GPU）",
     "desc": "tensorflow 完整版 + decimer-segmentation 先切分后识别（预留）"},
]


class OcsrBackendUpdate(BaseModel):
    backend: str = Field(default="auto", pattern="^(auto|molscribe|decimer)$")


@router.get("/settings/ocsr")
def get_ocsr() -> dict:
    from ..services.ocsr import availability

    s = get_settings()
    return {
        "current": s.ocsr_backend,
        "choices": OCSR_BACKEND_CHOICES,
        "status": availability(),
    }


@router.post("/settings/ocsr")
def set_ocsr_backend(body: OcsrBackendUpdate) -> dict:
    import os
    from ..services.secrets_store import write_env_updates

    os.environ["FORMUMIND_OCSR_BACKEND"] = body.backend
    try:
        write_env_updates({"FORMUMIND_OCSR_BACKEND": body.backend})
    except OSError:
        pass  # read-only FS — live process env still applied
    get_settings.cache_clear()
    return {"backend": body.backend, "status": "ok"}

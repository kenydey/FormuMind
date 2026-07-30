"""GET/POST /api/settings — Runtime LLM + API secrets configuration."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..config import get_settings
from ..services.llm import PROVIDERS, _provider_default_base_url, list_remote_models, test_connection
from ..services.llm_roles import CUSTOM_PROVIDER, normalize_base_url
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


@router.get("/settings")
def get_llm_settings():
    s = get_settings()
    return {
        "provider": effective_setting(s, "llm_provider"),
        "model": effective_setting(s, "llm_model"),
        "key_set": bool(s.get_active_api_key()),
        "base_url": effective_setting(s, "llm_base_url"),
        "providers": PROVIDERS,
    }


@router.post("/settings")
def update_llm_settings(update: LLMSettingsUpdate):
    _apply_llm_update(update)
    return test_connection()


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

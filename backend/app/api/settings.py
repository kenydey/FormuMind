"""GET/POST /api/settings — Runtime LLM + API secrets configuration."""
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..config import get_settings
from ..services.llm import PROVIDERS, _provider_default_base_url, test_connection
from ..services.runtime_secrets import effective_setting, get_runtime_secrets
from ..services.secrets_store import (
    SECRET_REGISTRY,
    list_secret_status,
    persist_llm_runtime,
    probe_secret,
    update_secrets,
)

router = APIRouter()


class LLMSettingsUpdate(BaseModel):
    """Accept both snake_case and camelCase from the frontend."""

    model_config = ConfigDict(populate_by_name=True)

    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = Field(default=None, alias="baseUrl")


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

    if update.api_key is not None:
        provider = str(update.provider or effective_setting(s, "llm_provider"))
        key_field = f"{provider}_api_key"
        if key_field in _SECRET_ATTR_IDS:
            update_secrets({key_field: update.api_key})
        elif provider == "anthropic":
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

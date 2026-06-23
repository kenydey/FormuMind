"""GET/POST /api/settings — Runtime LLM configuration."""
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from ..config import get_settings
from ..services.llm import PROVIDERS, _provider_default_base_url, test_connection

router = APIRouter()


class LLMSettingsUpdate(BaseModel):
    """Accept both snake_case and camelCase from the frontend."""

    model_config = ConfigDict(populate_by_name=True)

    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = Field(default=None, alias="baseUrl")


@router.get("/settings")
def get_llm_settings():
    s = get_settings()
    return {
        "provider": s.llm_provider,
        "model": s.llm_model,
        "key_set": bool(s.get_active_api_key()),
        "base_url": s.llm_base_url,
        "providers": PROVIDERS,
    }


@router.post("/settings")
def update_llm_settings(update: LLMSettingsUpdate):
    s = get_settings()
    # Mutate in-memory (no file persistence; restart needed for env-var durability).
    provider_changed = (
        update.provider is not None and update.provider != s.llm_provider
    )
    if update.provider is not None:
        s.llm_provider = update.provider
    if update.model is not None:
        s.llm_model = update.model
    if provider_changed and update.base_url is None:
        # Drop stale base URL from a previous provider unless the client overrides it.
        s.llm_base_url = _provider_default_base_url(s.llm_provider)
    if update.base_url is not None:
        s.llm_base_url = update.base_url.strip() or _provider_default_base_url(s.llm_provider)
    if update.api_key is not None:
        # Store the key under the current provider's attribute.
        key_field = f"{s.llm_provider}_api_key"
        if hasattr(s, key_field):
            object.__setattr__(s, key_field, update.api_key)
        elif s.llm_provider == "anthropic":
            object.__setattr__(s, "anthropic_api_key", update.api_key)
    return test_connection()


@router.post("/settings/test")
def test_llm_connection():
    return test_connection()

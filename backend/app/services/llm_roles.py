"""Model *roles* — one resolver for "which model answers this kind of call".

FormuMind used to have a single global LLM choice, and vision borrowed the whole
thing: provider, API key and base_url all came from the chat configuration, and
only the *model name* could differ. That is broken rather than merely
inconvenient, because the best text model and a model that can read a picture
are frequently not the same vendor — DeepSeek rejects an ``image_url`` content
part outright (``unknown variant 'image_url'``), so selecting it for text
silently killed the entire chemical-figure path with nothing in the UI to say so.

So a call now names a *role* and this module answers with everything the call
needs. Two roles exist today:

``text``
    The existing global configuration. Unchanged.
``vision``
    Independent provider / model / key / base_url / timeout. When
    ``vision_provider`` is empty it inherits the text role completely, which is
    exactly the historical behaviour — existing installs see no change.

The inheritance rule is deliberately all-or-nothing on the *connection*: a
half-inherited config (vision provider from one place, key from another) is how
you get a call that authenticates against the wrong vendor. Only the model name,
timeout and token budget layer on top, because those are per-role tuning rather
than identity.

Dependency direction is one-way: ``vision_extract → llm_roles → llm``. ``llm.py``
does not import this module, so there is no cycle to work around.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse, urlunparse

from ..config import PROVIDER_KEY_ATTR, get_settings
from .runtime_secrets import effective_setting

TEXT = "text"
VISION = "vision"
ROLES = (TEXT, VISION)

#: The generic OpenAI-compatible endpoint (HF Inference Endpoints, vLLM, TGI).
CUSTOM_PROVIDER = "custom"


@dataclass(frozen=True)
class RoleConfig:
    """Everything one LLM call needs, resolved for a single role."""

    role: str
    provider: str
    model: str
    api_key: str
    base_url: str | None
    timeout: float
    max_tokens: int
    #: Extra HTTP headers for the provider (cold-start hold on custom endpoints).
    extra_headers: dict[str, str] = field(default_factory=dict)
    #: True when this role is following another role's connection settings.
    inherited: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


def normalize_base_url(url: str | None) -> str | None:
    """Append ``/v1`` when a bare host was given.

    HuggingFace Inference Endpoints hand you ``https://<name>.endpoints.
    huggingface.cloud`` but the OpenAI-compatible route lives under ``/v1``, and
    pasting the URL as shown is the obvious mistake. Only an *empty* path is
    filled in: a URL that already names a path may well be a deliberate prefix
    route, and rewriting it would break a working configuration to fix a
    hypothetical one.
    """
    raw = (url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw
    path = parsed.path.rstrip("/")
    if path:
        return urlunparse(parsed._replace(path=path))
    return urlunparse(parsed._replace(path="/v1"))


def _provider_base_url(provider: str, override: str | None) -> str | None:
    """Catalog default unless overridden, then normalized."""
    from .llm import _resolve_openai_base_url

    return normalize_base_url(_resolve_openai_base_url(provider, override))


def _key_for_provider(settings, provider: str) -> str:
    attr = PROVIDER_KEY_ATTR.get(provider)
    if not attr:
        return ""
    return str(effective_setting(settings, attr) or "")


def _scale_up_headers(settings, provider: str, timeout: float) -> dict[str, str]:
    """``X-Scale-Up-Timeout`` for custom endpoints only.

    A scale-to-zero endpoint answers 503 while its replica boots; this header
    makes the provider's proxy hold the request until it is ready instead. It is
    capped below our own client timeout — if the client gave up first we would
    have paid the whole cold start and still thrown the answer away.
    """
    if provider != CUSTOM_PROVIDER:
        return {}
    configured = int(getattr(settings, "vision_scale_up_timeout", 0) or 0)
    if configured <= 0:
        return {}
    budget = int(max(1.0, timeout - 30.0))
    return {"X-Scale-Up-Timeout": str(min(configured, budget))}


def _text_config(settings) -> RoleConfig:
    provider = str(effective_setting(settings, "llm_provider") or "")
    override = effective_setting(settings, "llm_base_url")
    if provider == CUSTOM_PROVIDER and not override:
        override = effective_setting(settings, "custom_base_url")
    timeout = float(settings.llm_timeout_seconds)
    return RoleConfig(
        role=TEXT,
        provider=provider,
        model=str(effective_setting(settings, "llm_model") or ""),
        api_key=_key_for_provider(settings, provider),
        base_url=_provider_base_url(provider, override),
        timeout=timeout,
        max_tokens=int(settings.llm_max_tokens),
        extra_headers=_scale_up_headers(settings, provider, timeout),
    )


def _vision_config(settings) -> RoleConfig:
    provider = str(effective_setting(settings, "vision_provider") or "").strip()
    model_override = str(effective_setting(settings, "vision_model") or "").strip()

    if not provider:
        # Inherit the text connection wholesale — historical behaviour. Note the
        # timeout and token budget also stay on the text values here: a role that
        # is merely riding the chat provider should behave exactly as before,
        # and the patient vision timeout exists for deliberately separate
        # endpoints that can cold-start.
        text = _text_config(settings)
        return RoleConfig(
            role=VISION,
            provider=text.provider,
            model=model_override or text.model,
            api_key=text.api_key,
            base_url=text.base_url,
            timeout=text.timeout,
            max_tokens=text.max_tokens,
            extra_headers=text.extra_headers,
            inherited=True,
        )

    override = effective_setting(settings, "vision_base_url")
    if provider == CUSTOM_PROVIDER and not override:
        override = effective_setting(settings, "custom_base_url")
    # Explicit key first, then the shared per-provider secret — so "text on
    # DeepSeek, vision on OpenAI" does not make you type the OpenAI key twice.
    api_key = str(effective_setting(settings, "vision_api_key") or "") or _key_for_provider(
        settings, provider
    )
    timeout = float(settings.vision_timeout_seconds or settings.llm_timeout_seconds)
    max_tokens = int(settings.vision_max_tokens or settings.llm_max_tokens)
    return RoleConfig(
        role=VISION,
        provider=provider,
        model=model_override,
        api_key=api_key,
        base_url=_provider_base_url(provider, override),
        timeout=timeout,
        max_tokens=max_tokens,
        extra_headers=_scale_up_headers(settings, provider, timeout),
    )


def resolve_role(role: str) -> RoleConfig:
    """Resolve a role to a concrete connection. Raises on an unknown role."""
    settings = get_settings()
    if role == TEXT:
        return _text_config(settings)
    if role == VISION:
        return _vision_config(settings)
    raise ValueError(f"unknown model role: {role!r}")

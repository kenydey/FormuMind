"""Model role resolution — text vs vision.

Two invariants carry this module, and they point in opposite directions:

*Inheritance*: with ``vision_provider`` empty the vision role must resolve to the
text connection field for field. That is what makes the whole change a no-op for
existing installs.

*Isolation*: with ``vision_provider`` set, none of the text connection may leak
through. This is the one that protects against the failure the feature exists to
fix — a vision call that authenticates against the text vendor looks configured
and fails at the API, which is how "I switched vision to OpenAI" silently keeps
talking to DeepSeek.
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.services import llm_roles
from app.services.llm_roles import TEXT, VISION, normalize_base_url, resolve_role
from app.services.runtime_secrets import get_runtime_secrets


@pytest.fixture(autouse=True)
def _clean_overlay():
    rs = get_runtime_secrets()
    rs.clear()
    get_settings.cache_clear()
    yield
    rs.clear()
    get_settings.cache_clear()


def _set(**overrides):
    rs = get_runtime_secrets()
    for attr, value in overrides.items():
        rs.set(attr, value)


# ── inheritance ──────────────────────────────────────────────────────────────


def test_vision_inherits_the_whole_text_connection_by_default():
    _set(
        llm_provider="openai",
        llm_model="gpt-4o",
        openai_api_key="sk-text",
        vision_provider="",
    )
    text = resolve_role(TEXT)
    vision = resolve_role(VISION)

    assert vision.inherited is True
    assert (vision.provider, vision.api_key, vision.base_url) == (
        text.provider,
        text.api_key,
        text.base_url,
    )
    assert vision.model == text.model
    # A role merely riding the chat provider must behave exactly as before —
    # the patient vision timeout is for deliberately separate endpoints.
    assert vision.timeout == text.timeout
    assert vision.max_tokens == text.max_tokens


def test_vision_model_overrides_without_changing_the_connection():
    _set(
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        openai_api_key="sk-text",
        vision_provider="",
        vision_model="gpt-4o",
    )
    text = resolve_role(TEXT)
    vision = resolve_role(VISION)

    assert vision.model == "gpt-4o"
    assert vision.inherited is True
    assert vision.api_key == text.api_key
    assert vision.provider == text.provider


# ── isolation ────────────────────────────────────────────────────────────────


def test_explicit_vision_provider_does_not_leak_the_text_key():
    # The exact scenario this feature exists for: the user's text model cannot
    # read images, so vision points somewhere else.
    _set(
        llm_provider="deepseek",
        llm_model="deepseek-v4-pro",
        deepseek_api_key="sk-deepseek",
        vision_provider="openai",
        vision_model="gpt-4o",
        openai_api_key="sk-openai",
    )
    text = resolve_role(TEXT)
    vision = resolve_role(VISION)

    assert text.provider == "deepseek" and text.api_key == "sk-deepseek"
    assert vision.provider == "openai"
    assert vision.api_key == "sk-openai"
    assert vision.api_key != text.api_key
    assert vision.base_url != text.base_url
    assert vision.inherited is False


def test_vision_api_key_wins_over_the_shared_provider_secret():
    _set(
        llm_provider="deepseek",
        deepseek_api_key="sk-deepseek",
        vision_provider="openai",
        openai_api_key="sk-shared",
        vision_api_key="sk-dedicated",
    )
    assert resolve_role(VISION).api_key == "sk-dedicated"


def test_vision_falls_back_to_the_shared_provider_secret():
    # So "text on DeepSeek, vision on OpenAI" does not need the OpenAI key typed
    # a second time.
    _set(
        llm_provider="deepseek",
        deepseek_api_key="sk-deepseek",
        vision_provider="openai",
        openai_api_key="sk-shared",
        vision_api_key="",
    )
    assert resolve_role(VISION).api_key == "sk-shared"


def test_explicit_vision_role_uses_the_patient_timeout():
    _set(
        llm_provider="deepseek",
        deepseek_api_key="sk-deepseek",
        vision_provider="openai",
        openai_api_key="sk-openai",
    )
    text = resolve_role(TEXT)
    vision = resolve_role(VISION)
    assert vision.timeout > text.timeout


# ── custom endpoint ──────────────────────────────────────────────────────────


def test_custom_provider_resolves_its_own_key_and_base_url():
    _set(
        vision_provider="custom",
        vision_model="tgi",
        vision_api_key="hf_token",
        vision_base_url="https://abc.endpoints.huggingface.cloud/v1",
    )
    cfg = resolve_role(VISION)
    assert cfg.provider == "custom"
    assert cfg.api_key == "hf_token"
    assert cfg.base_url == "https://abc.endpoints.huggingface.cloud/v1"
    assert cfg.model == "tgi"


def test_custom_text_role_reads_custom_api_key():
    # PROVIDER_KEY_ATTR had no "custom" entry, so this used to resolve to an
    # empty key and present as "no key configured" rather than a missing mapping.
    _set(
        llm_provider="custom",
        llm_model="tgi",
        custom_api_key="hf_token",
        custom_base_url="https://abc.endpoints.huggingface.cloud/v1",
    )
    cfg = resolve_role(TEXT)
    assert cfg.api_key == "hf_token"
    assert cfg.configured is True
    assert cfg.base_url == "https://abc.endpoints.huggingface.cloud/v1"


def test_scale_up_header_only_on_custom_endpoints():
    _set(
        vision_provider="custom",
        vision_api_key="hf_token",
        vision_base_url="https://abc.endpoints.huggingface.cloud/v1",
    )
    assert "X-Scale-Up-Timeout" in resolve_role(VISION).extra_headers

    _set(vision_provider="openai", openai_api_key="sk-openai")
    assert resolve_role(VISION).extra_headers == {}


def test_scale_up_header_capped_below_the_client_timeout():
    # Holding longer than our own timeout means paying the whole cold start and
    # then discarding the answer.
    _set(
        vision_provider="custom",
        vision_api_key="hf_token",
        vision_base_url="https://abc.endpoints.huggingface.cloud/v1",
    )
    cfg = resolve_role(VISION)
    held = int(cfg.extra_headers["X-Scale-Up-Timeout"])
    assert held < cfg.timeout


def test_scale_up_header_disabled_by_zero(monkeypatch):
    monkeypatch.setattr(get_settings(), "vision_scale_up_timeout", 0, raising=False)
    _set(
        vision_provider="custom",
        vision_api_key="hf_token",
        vision_base_url="https://abc.endpoints.huggingface.cloud/v1",
    )
    assert resolve_role(VISION).extra_headers == {}


# ── base_url normalization ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        # The URL HF actually shows you, which omits the OpenAI-compatible route.
        ("https://abc.endpoints.huggingface.cloud", "https://abc.endpoints.huggingface.cloud/v1"),
        ("https://abc.endpoints.huggingface.cloud/", "https://abc.endpoints.huggingface.cloud/v1"),
        # Already correct — left alone.
        ("https://abc.endpoints.huggingface.cloud/v1", "https://abc.endpoints.huggingface.cloud/v1"),
        # A deliberate prefix route must not be rewritten.
        ("https://gw.example.com/llm/v1", "https://gw.example.com/llm/v1"),
        ("https://gw.example.com/openai/v1/", "https://gw.example.com/openai/v1"),
        (None, None),
        ("", None),
    ],
)
def test_normalize_base_url(raw, expected):
    assert normalize_base_url(raw) == expected


def test_unknown_role_is_an_error():
    with pytest.raises(ValueError):
        resolve_role("embedding")


def test_roles_tuple_matches_the_resolver():
    for role in llm_roles.ROLES:
        assert resolve_role(role).role == role

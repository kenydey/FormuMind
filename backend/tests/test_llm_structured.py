"""Tests for complete_structured and offline recommend wrapper."""
from __future__ import annotations

from app.domain.formulation_gate import formulation_to_recommended, offline_recommend_response
from app.domain.knowledge import offline_recommend_fallback
from app.domain.schemas import ProductDomain, RecommendedFormulaListResponse, Requirement
from pydantic import BaseModel

from app.services import llm


def test_offline_recommend_response_wraps_cas():
    req = Requirement(domain=ProductDomain.anticorrosion_coating, salt_spray_hours=500)
    forms = offline_recommend_fallback(req, n=2)
    resp = offline_recommend_response(forms, reason="test")
    assert resp.engine == "offline"
    assert len(resp.formulas) == 2
    assert resp.formulas[0].components


def test_formulation_to_recommended_has_mf_or_cas():
    req = Requirement(domain=ProductDomain.anticorrosion_coating)
    forms = offline_recommend_fallback(req, n=1)
    rec = formulation_to_recommended(forms[0], engine="offline")
    assert rec.components
    assert rec.components[0].name


def test_complete_structured_no_key_returns_none():
    parsed, err = llm.complete_structured(
        "system",
        "user",
        RecommendedFormulaListResponse,
    )
    assert parsed is None
    assert err


# ── providers that accept the chat API but not response_format ───────────────


class _Row(BaseModel):
    metric: str
    value: float


def _openai_error(message: str) -> Exception:
    return Exception(message)


def test_deepseeks_refusal_is_recognised_as_unsupported() -> None:
    """DeepSeek answers 'This response_format type is unavailable now' with a
    400. Classified as transient it was retried three times — three requests
    and ~6s spent on an outcome that could never change."""
    assert llm._is_structured_unsupported(
        _openai_error(
            "Error code: 400 - {'error': {'message': 'This response_format type "
            "is unavailable now', 'type': 'invalid_request_error'}}"
        )
    )


def test_a_genuinely_bad_request_is_not_mistaken_for_unsupported() -> None:
    """Falling back on every 400 would hide real request errors behind a
    second call that fails differently."""
    assert not llm._is_structured_unsupported(
        _openai_error("Error code: 400 - invalid value for 'temperature'")
    )
    assert not llm._is_structured_unsupported(_openai_error("connection reset"))


def test_unsupported_structured_output_falls_back_to_prompt_json(monkeypatch) -> None:
    """The fallback is the same schema-in-the-prompt approach Anthropic and
    Gemini already use, so a provider without native structured output
    degrades to a working extraction rather than to nothing."""
    calls: list[str] = []

    def native(*_args, **_kwargs):
        calls.append("native")
        raise llm.LLMStructuredUnsupported("This response_format type is unavailable now")

    def prompted(*_args, **_kwargs):
        calls.append("prompted")
        return _Row(metric="salt_spray_hours", value=1080.0)

    monkeypatch.setattr(llm, "_openai_structured_request", native)
    monkeypatch.setattr(llm, "_openai_prompt_structured_request", prompted)

    row = llm._invoke_structured_once(
        "sys", "user", _Row,
        provider="deepseek", api_key="k", model="deepseek-v4-pro",
        max_tokens=512, base_url=None, schema={},
    )
    assert calls == ["native", "prompted"]      # tried once, then fell back
    assert row.value == 1080.0


def test_the_fallback_is_not_retried_three_times(monkeypatch) -> None:
    """LLMStructuredUnsupported must be handled below the retry decorator —
    retrying a permanent 400 is pure waste."""
    attempts: list[int] = []

    def native(*_args, **_kwargs):
        attempts.append(1)
        raise llm.LLMStructuredUnsupported("response_format is unavailable")

    monkeypatch.setattr(llm, "_openai_structured_request", native)
    monkeypatch.setattr(
        llm, "_openai_prompt_structured_request",
        lambda *a, **k: _Row(metric="m", value=1.0),
    )
    llm._invoke_structured_once(
        "sys", "user", _Row,
        provider="deepseek", api_key="k", model="deepseek-v4-pro",
        max_tokens=512, base_url=None, schema={},
    )
    assert len(attempts) == 1

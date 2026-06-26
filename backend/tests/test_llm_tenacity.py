"""Tenacity retry behaviour for OpenAI-compatible LLM transport."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from app.services import llm


@pytest.fixture(autouse=True)
def _stub_openai_module():
    """The transport does ``from openai import OpenAI``. Provide a stub module
    when the optional SDK isn't installed so ``patch("openai.OpenAI", ...)``
    resolves and the retry logic is exercised offline."""
    if "openai" in sys.modules:
        yield
        return
    stub = types.ModuleType("openai")
    stub.OpenAI = object  # replaced per-test via patch("openai.OpenAI", ...)
    sys.modules["openai"] = stub
    try:
        yield
    finally:
        sys.modules.pop("openai", None)


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.reasoning_content = None
        self.model_extra = {}


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


def test_openai_compatible_retries_on_timeout_then_succeeds():
    calls: list[int] = []

    def create_side_effect(**kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("simulated timeout")
        return _FakeResponse("OK")

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = create_side_effect

    with patch("openai.OpenAI", return_value=mock_client):
        text, err = llm._complete_openai_compatible_detail(
            "Reply with exactly: OK",
            "test-key",
            "gpt-4o-mini",
            16,
            None,
            probe=True,
        )

    assert err is None
    assert text == "OK"
    assert len(calls) == 3


def test_openai_compatible_does_not_retry_auth_errors():
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = Exception("401 Authentication failed")

    with patch("openai.OpenAI", return_value=mock_client):
        text, err = llm._complete_openai_compatible_detail(
            "hello",
            "bad-key",
            "gpt-4o-mini",
            16,
            None,
        )

    assert text is None
    assert err is not None
    assert mock_client.chat.completions.create.call_count == 1


def test_complete_structured_retries_validation(monkeypatch):
    """Validation failures trigger structured retry (same prompt, no fix_prompt)."""
    attempts: list[int] = []

    def fake_invoke(*args, **kwargs):
        attempts.append(1)
        if len(attempts) < 2:
            raise llm.LLMValidationError("bad json")
        from pydantic import BaseModel

        class Demo(BaseModel):
            value: str

        return Demo(value="ok")

    monkeypatch.setattr(llm, "_invoke_structured_once", fake_invoke)
    monkeypatch.setattr(
        llm,
        "get_settings",
        lambda: type(
            "S",
            (),
            {
                "llm_provider": "openai",
                "get_active_api_key": lambda self: "k",
                "llm_model": "gpt-4o-mini",
                "llm_max_tokens": 64,
                "llm_base_url": None,
            },
        )(),
    )

    from pydantic import BaseModel

    class Demo(BaseModel):
        value: str

    parsed, err = llm.complete_structured("sys", "user", Demo, retry=True)
    assert err is None
    assert parsed is not None
    assert parsed.value == "ok"
    assert len(attempts) == 2

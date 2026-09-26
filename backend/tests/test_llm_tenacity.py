"""Tenacity retry behaviour for OpenAI-compatible LLM transport."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from app.services import llm


def _install_openai_stub() -> object | None:
    """Force a patchable ``openai.OpenAI`` into ``sys.modules``.

    Returns the previous ``sys.modules['openai']`` entry (or ``None``) so the
    caller can restore it. Always installs a fresh stub — never trust a real
    install: chemcrow can pin ``openai==0.27.8`` which has no ``OpenAI`` class,
    and ``patch("openai.OpenAI")`` then raises ``AttributeError`` during setup
    (before any retry logic runs).
    """
    previous = sys.modules.get("openai")
    stub = types.ModuleType("openai")
    stub.OpenAI = object  # replaced per-test via patch("openai.OpenAI", ...)
    sys.modules["openai"] = stub
    return previous


def _restore_openai_module(previous: object | None) -> None:
    if previous is None:
        sys.modules.pop("openai", None)
    else:
        sys.modules["openai"] = previous  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _stub_openai_module():
    """Keep ``patch("openai.OpenAI", ...)`` hermetic for every test.

    The transport does ``from openai import OpenAI`` at call time. Offline CI
    may lack the SDK; full-suite runs may already have a legacy openai (0.27.x,
    no ``OpenAI``) imported by chemcrow paths. Always stub + restore so these
    unit tests do not depend on import order or the installed openai major.
    """
    previous = _install_openai_stub()
    try:
        yield
    finally:
        _restore_openai_module(previous)


def test_openai_stub_overrides_legacy_sdk_without_OpenAI():
    """Regression: openai 0.27.x already in sys.modules must not break patch."""
    legacy = types.ModuleType("openai")
    assert not hasattr(legacy, "OpenAI")
    sys.modules["openai"] = legacy
    previous = _install_openai_stub()
    try:
        mock_client = MagicMock()
        with patch("openai.OpenAI", return_value=mock_client) as factory:
            from openai import OpenAI

            assert OpenAI() is mock_client
            factory.assert_called_once_with()
    finally:
        _restore_openai_module(previous)
    # Helper restored the legacy module we planted (not the autouse stub).
    assert sys.modules.get("openai") is legacy


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
    # 对齐 2026-09-04 产品决策(commit 7799467): _LLM_RETRY 为 2 次尝试
    # (初次 + 1 重试), 避免 deepseek 慢窗口下单步无限挂。
    calls: list[int] = []

    def create_side_effect(**kwargs):
        calls.append(1)
        if len(calls) < 2:
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
    assert len(calls) == 2


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

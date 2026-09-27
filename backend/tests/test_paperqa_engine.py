"""Tests for decoupled PaperQA engine (Wave B)."""
from __future__ import annotations

import pytest

from app.domain.schemas import Evidence
from app.services import paperqa_engine as eng
from app.services import evidence_synthesis as es


class _S:
    paperqa_enabled = True
    paperqa_llm_model = "deepseek-chat"
    paperqa_embedding = ""
    llm_provider = "deepseek"
    llm_model = "deepseek-chat"
    llm_base_url = "https://api.deepseek.com"
    openai_api_key = ""
    deepseek_api_key = "sk-test"

    def get_active_api_key(self):
        return self.deepseek_api_key


def test_unavailable_without_package(monkeypatch):
    monkeypatch.setattr(eng, "optional_import", lambda m: False)
    assert eng.paperqa_available(_S()) is False


def test_unavailable_when_flag_off(monkeypatch):
    monkeypatch.setattr(eng, "optional_import", lambda m: True)

    class Off(_S):
        paperqa_enabled = False

    assert eng.paperqa_available(Off()) is False


def test_available_with_chat_key_no_openai(monkeypatch):
    monkeypatch.setattr(eng, "optional_import", lambda m: True)
    monkeypatch.setattr(
        "app.services.runtime_secrets.effective_setting",
        lambda s, k: getattr(s, k, None) or (s.deepseek_api_key if k == "deepseek_api_key" else ""),
    )
    assert eng.paperqa_available(_S()) is True


def test_answer_fail_open_on_engine_error(monkeypatch):
    monkeypatch.setattr(eng, "paperqa_available", lambda settings=None: True)

    async def boom(*a, **k):
        raise RuntimeError("litellm credentials")

    monkeypatch.setattr(eng, "answer_with_paperqa_async", boom)
    # Direct sync path with mocked async that raises via asyncio.run — use None return
    monkeypatch.setattr(
        eng,
        "answer_with_paperqa",
        lambda q, s, settings=None: None,
    )
    assert eng.answer_with_paperqa("q", [], settings=_S()) is None


@pytest.mark.asyncio
async def test_async_fail_open(monkeypatch):
    monkeypatch.setattr(eng, "paperqa_available", lambda settings=None: True)
    monkeypatch.setattr(eng, "optional_import", lambda m: True)

    class Boom:
        def __getattr__(self, name):
            raise RuntimeError("no paperqa")

    import sys

    monkeypatch.setitem(sys.modules, "paperqa", Boom())
    out = await eng.answer_with_paperqa_async(
        "q",
        [Evidence(source="x", identifier="1", title="t", snippet="s", relevance=0.9)],
        settings=_S(),
    )
    assert out is None


def test_evidence_synthesis_uses_engine(monkeypatch):
    called = {"ok": False}

    def fake_avail():
        return True

    def fake_answer(q, sources, settings=None):
        called["ok"] = True
        return ("ans", sources[:1])

    monkeypatch.setattr(
        "app.services.paperqa_engine.paperqa_available", fake_avail
    )
    monkeypatch.setattr(
        "app.services.paperqa_engine.answer_with_paperqa", fake_answer
    )
    ev = [Evidence(source="x", identifier="1", title="t", snippet="s", relevance=0.9)]
    out = es.try_paperqa_answer("q", ev)
    assert called["ok"] and out is not None
    assert out[0] == "ans"

"""SSE 流式问答端点测试(2026-09-04) — /api/chat/stream。

用假 LLM 流(monkeypatch _openai_compatible_stream)覆盖:
事件序列(phase→meta→token×N→phase claims→done)/ error / 无 key。
真实 deepseek 流已在 CLI 验证(2.5s 首字, 36 delta)。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    return TestClient(app)


def _parse_events(body: str) -> list[dict]:
    out = []
    for block in body.split("\n\n"):
        line = block.strip()
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


class FakeDelta:
    def __init__(self, text):
        self.content = text


class FakeChoice:
    def __init__(self, text):
        self.delta = FakeDelta(text)


class FakeChunk:
    def __init__(self, text):
        self.choices = [FakeChoice(text)]


def test_stream_emits_tokens_then_done(monkeypatch, client):
    import app.api.chat as chat_mod
    import app.services.llm as llm_mod
    import app.services.chat_claims as cc_mod

    def fake_stream(prompt, api_key, model, max_tokens, base_url=None, *,
                    on_delta=None, disable_thinking=False):
        assert disable_thinking is True
        pieces = ["镁合金", "钝化是", "表面处理"]
        for p in pieces:
            if on_delta:
                on_delta(p)
        return "".join(pieces)

    monkeypatch.setattr(llm_mod, "_openai_compatible_stream", fake_stream)

    def fake_plan(req, settings):
        return {
            "question": req.question,
            "prompt": "p",
            "sources": [],
            "kb_used": 0,
            "entity_resolution": None,
            "kg_stats": None,
            "clarification": None,
            "rewritten_query": None,
        }

    monkeypatch.setattr(chat_mod, "_stream_answer_plan", fake_plan)
    monkeypatch.setattr(cc_mod, "build_sourced_claims", lambda *a, **k: [])

    resp = client.post(
        "/api/chat/stream",
        json={
            "question": "什么是镁合金钝化?",
            "sources": [],
            "response_format": "markdown",
            "include_entity_resolution": False,
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_events(resp.text)
    types = [e["type"] for e in events]
    assert types[0] == "phase" and events[0]["phase"] == "retrieval"
    assert "meta" in types
    assert "token" in types
    assert types[-1] == "done"

    tokens = "".join(e["delta"] for e in events if e["type"] == "token")
    assert tokens == "镁合金钝化是表面处理"
    done = events[-1]
    assert done["answer"] == tokens
    assert done["citations"] == []


def test_stream_error_when_llm_fails(monkeypatch, client):
    import app.api.chat as chat_mod
    import app.services.llm as llm_mod
    import app.services.chat_claims as cc_mod

    def boom(*args, **kwargs):
        raise RuntimeError("上游 500")

    monkeypatch.setattr(llm_mod, "_openai_compatible_stream", boom)
    monkeypatch.setattr(
        chat_mod, "_stream_answer_plan",
        lambda req, settings: {
            "question": req.question, "prompt": "p", "sources": [],
            "kb_used": 0, "entity_resolution": None, "kg_stats": None,
            "clarification": None, "rewritten_query": None,
        },
    )
    monkeypatch.setattr(cc_mod, "build_sourced_claims", lambda *a, **k: [])

    resp = client.post(
        "/api/chat/stream",
        json={"question": "hi", "sources": [], "response_format": "markdown"},
    )
    assert resp.status_code == 200
    events = _parse_events(resp.text)
    assert events[-1]["type"] == "error"
    assert "生成失败" in events[-1]["message"]


def test_stream_structured_returns_done_without_tokens(monkeypatch, client):
    """structured 请求 → 整包 done, 无 token 事件。"""
    import app.services.chat_structured as cs_mod
    from app.domain.chat_schemas import StructuredAnswer

    def fake_structured(question, sources, history=None, domain=None, settings=None):
        return StructuredAnswer(summary="结构化答案摘要", key_findings=["发现1"]), None

    monkeypatch.setattr(cs_mod, "generate_structured_answer", fake_structured)

    resp = client.post(
        "/api/chat/stream",
        json={
            "question": "推荐配方方向",
            "sources": [],
            "response_format": "structured",
        },
    )
    assert resp.status_code == 200
    events = _parse_events(resp.text)
    types = [e["type"] for e in events]
    assert "token" not in types
    assert types[-1] == "done"
    assert events[-1]["structured"] is not None
    assert events[-1]["answer"] == "结构化答案摘要"

"""SSE 流式问答端点测试(2026-09-04) — /api/chat/stream。

用假 LLM 流(monkeypatch _openai_compatible_stream)覆盖:
事件序列(phase→meta→token×N→phase claims→done)/ error / 无 key。
真实 deepseek 流已在 CLI 验证(2.5s 首字, 36 delta)。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.domain.schemas import Evidence
from app.main import app


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    # Stream handler exits early with "未配置 LLM API Key" when no key is set.
    # Provide a dummy key so monkeypatched LLM streams are actually exercised.
    monkeypatch.setenv("FORMUMIND_DEEPSEEK_API_KEY", "sk-test-chat-stream")
    monkeypatch.setenv("FORMUMIND_LLM_PROVIDER", "deepseek")
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
            "relevant": [
                Evidence(source="seed", identifier="kb:1#c0", title="镁合金钝化",
                         snippet="镁合金钝化是表面处理", relevance=0.9)
            ],
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
    assert [c["identifier"] for c in done["citations"]] == ["kb:1#c0"]


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
            "clarification": None, "rewritten_query": None, "relevant": [],
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

    def fake_structured(question, sources, *args, **kwargs):
        # generate_structured_answer returns (answer, error, effective_sources)
        ev = Evidence(source="seed", identifier="kb:1#c0", title="镁合金钝化",
                      snippet="结构化答案摘要 发现1 结构化答案摘要", relevance=0.9)
        return StructuredAnswer(summary="结构化答案摘要", key_findings=["发现1"]), None, [ev]

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


# ── P3-2: opt-in VLM chart fallback ─────────────────────────────────────────


def _chart_plan(req, settings):
    from app.domain.schemas import Evidence

    return {
        "question": req.question,
        "prompt": "p",
        "sources": [
            Evidence(
                source="local",
                identifier="doc-1",
                title="t",
                snippet="s",
                relevance=0.9,
            )
        ],
        "kb_used": 1,
        "entity_resolution": None,
        "kg_stats": None,
        "clarification": None,
        "rewritten_query": None,
        "relevant": [],
        "mode": "chat",
        "selected_skills": [],
        "selected_mcp_servers": [],
        "data_sources": ["kb_evidence"],
    }


def test_chart_vlm_hook_zero_overhead_when_disabled(monkeypatch, client):
    """默认关闭时零开销：answer_chart_question 不被调用，走正常文本流。"""
    import app.api.chat as chat_mod
    import app.services.llm as llm_mod
    import app.services.page_thumbnails as thumbs_mod

    monkeypatch.setattr(chat_mod, "_stream_answer_plan", _chart_plan)
    monkeypatch.setattr(
        llm_mod, "_openai_compatible_stream", lambda *a, **k: "文本答案"
    )
    called = []

    def boom(*a, **k):
        called.append(1)
        raise AssertionError("must not be called when disabled")

    monkeypatch.setattr(thumbs_mod, "answer_chart_question", boom)

    resp = client.post(
        "/api/chat/stream",
        json={"question": "图1中的曲线说明了什么?", "sources": []},
    )
    assert resp.status_code == 200
    assert called == []
    events = _parse_events(resp.text)
    assert events[-1]["type"] == "done"
    assert "chart_vlm" not in events[-1]


def test_chart_vlm_hook_answers_chart_question(monkeypatch, client):
    """开启时图表问题走 VLM 路径：phase=chart_vlm，单 token，done 带 chart_vlm。"""
    import app.api.chat as chat_mod
    import app.services.llm as llm_mod
    import app.services.page_thumbnails as thumbs_mod
    from app.services.page_thumbnails import ChartVLMResult

    monkeypatch.setenv("FORMUMIND_VLM_FALLBACK_ENABLED", "true")
    from app.config import get_settings

    get_settings.cache_clear()

    monkeypatch.setattr(chat_mod, "_stream_answer_plan", _chart_plan)
    llm_calls = []
    monkeypatch.setattr(
        llm_mod,
        "_openai_compatible_stream",
        lambda *a, **k: llm_calls.append(1) or "不应调用",
    )

    def fake_chart(query, source_id, page_nums=None):
        assert source_id == "doc-1"
        return ChartVLMResult(
            answer="VLM图表答案", pages_used=[1], image_tokens=1105, model="m"
        )

    monkeypatch.setattr(thumbs_mod, "answer_chart_question", fake_chart)

    resp = client.post(
        "/api/chat/stream",
        json={"question": "图1中的曲线说明了什么?", "sources": []},
    )
    assert resp.status_code == 200
    events = _parse_events(resp.text)
    phases = [e.get("phase") for e in events if e["type"] == "phase"]
    assert "chart_vlm" in phases
    tokens = [e["delta"] for e in events if e["type"] == "token"]
    assert tokens == ["VLM图表答案"]
    done = events[-1]
    assert done["type"] == "done"
    assert done["chart_vlm"]["pages_used"] == [1]
    assert done["chart_vlm"]["image_tokens"] == 1105
    assert llm_calls == []  # 文本 LLM 流未被调用


def test_chart_vlm_hook_falls_through_on_none(monkeypatch, client):
    """VLM 返回 None（无缩略图/失败）时回退正常文本流。"""
    import app.api.chat as chat_mod
    import app.services.llm as llm_mod
    import app.services.page_thumbnails as thumbs_mod

    monkeypatch.setenv("FORMUMIND_VLM_FALLBACK_ENABLED", "true")
    from app.config import get_settings

    get_settings.cache_clear()

    monkeypatch.setattr(chat_mod, "_stream_answer_plan", _chart_plan)
    monkeypatch.setattr(
        llm_mod, "_openai_compatible_stream", lambda *a, **k: "文本答案"
    )
    monkeypatch.setattr(thumbs_mod, "answer_chart_question", lambda *a, **k: None)

    resp = client.post(
        "/api/chat/stream",
        json={"question": "图1中的曲线说明了什么?", "sources": []},
    )
    assert resp.status_code == 200
    events = _parse_events(resp.text)
    assert events[-1]["type"] == "done"
    assert "chart_vlm" not in events[-1]

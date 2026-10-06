"""/api/chat/stream must not run blocking post-processing on the event loop.

In evidence mode (or with selected skills) ``_finalize_evidence_fields`` does a
Crossref DOI lookup, an LLM reviewer call and possibly a repair generation —
seconds of blocking I/O. ``gen()`` is an ``async def`` generator and called it
directly, stalling the whole worker (other SSE streams, health checks) for the
duration. Its sibling step, ``_claims_and_audit``, already went through
``asyncio.to_thread``.
"""
from __future__ import annotations

import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app.domain.schemas import Evidence
from app.main import app


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_DEEPSEEK_API_KEY", "sk-test-chat-stream")
    monkeypatch.setenv("FORMUMIND_LLM_PROVIDER", "deepseek")
    # chem tools would call the provider (with the fake key) before the stubbed stream
    monkeypatch.setenv("FORMUMIND_CHAT_CHEM_TOOLS_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    yield TestClient(app)
    get_settings.cache_clear()


def _events(body: str) -> list[dict]:
    return [json.loads(b.strip()[6:]) for b in body.split("\n\n") if b.strip().startswith("data: ")]


def _stub_pipeline(monkeypatch):
    import app.api.chat as chat_mod
    import app.services.chat_claims as cc_mod
    import app.services.llm as llm_mod

    def fake_stream(prompt, api_key, model, max_tokens, base_url=None, *, on_delta=None, disable_thinking=False):
        for piece in ("镁合金", "钝化"):
            if on_delta:
                on_delta(piece)
        return "镁合金钝化"

    monkeypatch.setattr(llm_mod, "_openai_compatible_stream", fake_stream)
    monkeypatch.setattr(
        chat_mod,
        "_stream_answer_plan",
        lambda req, settings: {
            "question": req.question, "prompt": "p", "sources": [], "kb_used": 0,
            "entity_resolution": None, "kg_stats": None, "clarification": None, "rewritten_query": None,
            "relevant": [Evidence(source="seed", identifier="kb:1#c0", title="t", snippet="s", relevance=0.9)],
        },
    )
    monkeypatch.setattr(cc_mod, "build_sourced_claims", lambda *a, **k: [])


def test_finalize_evidence_fields_runs_off_the_event_loop(monkeypatch, client):
    import app.api.chat as chat_mod

    _stub_pipeline(monkeypatch)
    seen: dict = {}

    def spy(question, answer, citations, **kwargs):
        try:
            asyncio.get_running_loop()
            seen["on_event_loop"] = True
        except RuntimeError:
            seen["on_event_loop"] = False
        seen["thread"] = threading.current_thread().name
        seen["kwargs"] = sorted(kwargs)
        # v14-1: spy 返回 6 元组（a61c5d1 漏改）
        return answer + "·checked", {"10.1/x": "ok"}, None, None, None, []

    monkeypatch.setattr(chat_mod, "_finalize_evidence_fields", spy)

    resp = client.post(
        "/api/chat/stream",
        json={"question": "什么是钝化?", "sources": [], "response_format": "markdown"},
    )

    assert resp.status_code == 200
    events = _events(resp.text)
    assert events[-1]["type"] == "done"
    assert seen["on_event_loop"] is False, f"blocked the event loop (thread={seen['thread']})"
    # the keyword contract of the helper is unchanged by the offload
    assert {"settings", "mode", "selected_skills", "project_id", "sources", "domain", "history", "structure"} <= set(
        seen["kwargs"]
    )


def test_a_failing_finalize_does_not_break_the_stream_contract(monkeypatch, client):
    """Behaviour stays what it was: the helper's own try/except decides (here it
    raises, as an unexpected bug would) — the stream must still end in an event."""
    import app.api.chat as chat_mod

    _stub_pipeline(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("reviewer exploded")

    monkeypatch.setattr(chat_mod, "_finalize_evidence_fields", boom)
    resp = client.post(
        "/api/chat/stream", json={"question": "hi", "sources": [], "response_format": "markdown"}
    )
    assert resp.status_code == 200
    assert _events(resp.text)[-1]["type"] in {"done", "error"}

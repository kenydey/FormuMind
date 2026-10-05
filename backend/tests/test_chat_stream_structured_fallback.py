"""A structured request whose generation fails must fall back to the token stream (round-4).

``/api/chat`` (sync) falls back to a markdown answer when ``generate_structured_answer`` returns nothing.
The stream does the same *in intent* — it logs ``structured stream fallback`` and carries on — but the
``done`` event and its ``return`` sat one level too far out: they ran for ``structured is None`` as well,
read ``_claims`` / ``_audit`` / ``evidence_reviewer`` that are only bound inside ``if structured is not
None``, raised ``UnboundLocalError`` and ended the stream with ``{"type": "error"}``. Any provider hiccup
on a structured request (an invalid-JSON reply, a model without JSON mode) therefore showed the user an
error instead of an answer. The existing stream test only covers structured generation succeeding.
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
    monkeypatch.setenv("FORMUMIND_DEEPSEEK_API_KEY", "sk-test-chat-stream")
    monkeypatch.setenv("FORMUMIND_LLM_PROVIDER", "deepseek")
    from app.config import get_settings

    get_settings.cache_clear()
    yield TestClient(app)
    get_settings.cache_clear()


def _events(body: str) -> list[dict]:
    return [json.loads(block.strip()[6:]) for block in body.split("\n\n") if block.strip().startswith("data: ")]


def _plan(req, settings):
    return {
        "question": req.question, "prompt": "p", "sources": [], "kb_used": 0, "entity_resolution": None,
        "kg_stats": None, "clarification": None, "rewritten_query": None,
        "relevant": [Evidence(source="seed", identifier="kb:1#c0", title="镁合金钝化", snippet="镁合金钝化是表面处理", relevance=0.9)],
    }


def test_a_failed_structured_generation_falls_back_to_the_markdown_stream(monkeypatch, client):
    import app.api.chat as chat_mod
    import app.services.chat_chem_tools as tools_mod
    import app.services.chat_claims as claims_mod
    import app.services.chat_structured as structured_mod
    import app.services.llm as llm_mod

    monkeypatch.setattr(structured_mod, "generate_structured_answer", lambda *a, **k: (None, "provider returned invalid JSON", []))

    def fake_stream(prompt, api_key, model, max_tokens, base_url=None, *, on_delta=None, disable_thinking=False):
        for piece in ("镁合金", "钝化是", "表面处理"):
            if on_delta:
                on_delta(piece)
        return "镁合金钝化是表面处理"

    monkeypatch.setattr(llm_mod, "_openai_compatible_stream", fake_stream)
    monkeypatch.setattr(tools_mod, "should_enable_tools", lambda *a, **k: False)  # plain token stream, no tool loop
    monkeypatch.setattr(chat_mod, "_stream_answer_plan", _plan)
    monkeypatch.setattr(claims_mod, "build_sourced_claims", lambda *a, **k: [])

    resp = client.post(
        "/api/chat/stream",
        json={"question": "什么是镁合金钝化?", "sources": [], "response_format": "structured", "include_entity_resolution": False},
    )
    assert resp.status_code == 200
    events = _events(resp.text)
    types = [e["type"] for e in events]
    assert "error" not in types, events[-1]
    assert "token" in types and types[-1] == "done"
    done = events[-1]
    assert done["answer"] == "镁合金钝化是表面处理"
    assert done.get("structured") is None  # prose, not a structured object

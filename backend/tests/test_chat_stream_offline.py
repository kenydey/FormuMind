"""Chat without an LLM key must answer, on the path the UI actually uses (round-4).

QUICKSTART promises "with no key, everything still runs via the offline rule engine", and ``POST /api/chat``
keeps that promise: it answers with an excerpt of the loaded sources. The UI never calls it — it only streams
``/api/chat/stream``, which answered a missing key with ``{"type": "error", "message": "未配置 LLM API Key"}``.
The offline path existed and nothing could reach it. The stream now runs the same pipeline and delivers it as
one ``done`` event, with a notice that says the text is an excerpt rather than a generated answer.
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app

SOURCE = {
    "source": "seed",
    "identifier": "kb:1#c0",
    "title": "镁合金钝化",
    "snippet": "镁合金钝化是一种表面处理工艺，可在表面形成转化膜。",
    "relevance": 0.9,
}


@pytest.fixture()
def keyless(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("FORMUMIND_DEEPSEEK_API_KEY", "")  # overrides anything a developer's .env holds
    from app.config import get_settings

    get_settings.cache_clear()
    assert not get_settings().get_active_api_key()
    yield TestClient(app)
    get_settings.cache_clear()


def _events(body: str) -> list[dict]:
    return [json.loads(block.strip()[6:]) for block in body.split("\n\n") if block.strip().startswith("data: ")]


def _ask(client: TestClient, **extra) -> list[dict]:
    resp = client.post(
        "/api/chat/stream",
        json={"question": "什么是镁合金钝化?", "sources": [SOURCE], "include_entity_resolution": False, **extra},
    )
    assert resp.status_code == 200
    return _events(resp.text)


def test_no_key_streams_the_offline_excerpt_instead_of_an_error(keyless):
    events = _ask(keyless)
    types = [e["type"] for e in events]
    assert "error" not in types, events
    assert types[-1] == "done"
    done = events[-1]
    assert done["answer"].startswith("根据已加载资料：")
    assert "镁合金钝化" in done["answer"]
    assert done["citations"], "the excerpt must come with the source it quotes"


def test_the_excerpt_is_labelled_as_one(keyless):
    done = _ask(keyless)[-1]
    codes = [n["code"] for n in done["notices"]]
    assert "llm_offline" in codes
    assert "不是模型生成" in next(n["message"] for n in done["notices"] if n["code"] == "llm_offline")


def test_the_token_event_carries_the_same_text_as_done(keyless):
    events = _ask(keyless)
    tokens = [e["delta"] for e in events if e["type"] == "token"]
    assert "".join(tokens) == events[-1]["answer"]


def test_the_event_sequence_matches_what_the_ui_handles(keyless):
    """The UI reads phase → token → done; anything else would be dropped or mishandled."""
    types = [e["type"] for e in _ask(keyless)]
    assert set(types) <= {"phase", "meta", "token", "done"}
    assert types.index("token") < types.index("done")


def test_no_key_and_no_sources_abstains_rather_than_erroring(keyless):
    """Nothing to quote: the same abstention the sync endpoint gives, not a key error."""
    resp = keyless.post(
        "/api/chat/stream",
        json={"question": "什么是镁合金钝化?", "sources": [], "include_entity_resolution": False},
    )
    done = _events(resp.text)[-1]
    assert done["type"] == "done"
    assert "证据不足" in done["answer"]


def test_a_failure_in_the_offline_pipeline_is_reported_as_an_error_event(keyless, monkeypatch):
    import app.api.chat as chat_mod

    def boom(req):
        raise HTTPException(status_code=500, detail="问答处理失败")

    monkeypatch.setattr(chat_mod, "chat", boom)
    events = _ask(keyless)
    assert events[-1] == {"type": "error", "message": "问答处理失败"}

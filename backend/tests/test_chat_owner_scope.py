"""The caller's identity reaches the structured (Text2SQL) half of chat (round-5).

``hybrid_answer`` can scope the rows a generated statement may read to one owner, but chat never told it who was asking:
``chat`` and ``chat_stream`` took no ``Request``, so ``get_current_owner`` was unreachable and every caller was treated as
"restrict nothing". The route now binds the owner from the bearer token - and only from there: a client cannot name its
own owner in the body.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import chat as chat_api
from app.config import get_settings
from app.main import app

TOKENS = {"alice": "tok-alice", "bob": "tok-bob"}


@pytest.fixture()
def seen(monkeypatch):
    """Records what ``hybrid_answer`` is called with; everything else in chat is stubbed to be quick and offline."""
    calls: list[dict] = []
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_CHAT_CHEM_TOOLS_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_MULTI_USER", "true")
    monkeypatch.setenv("FORMUMIND_API_TOKENS_JSON", json.dumps(TOKENS))
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.llm.answer_question", lambda q, sources, domain=None, **kw: ("答案", sources[:1] or []))
    monkeypatch.setattr("app.services.kb_index.search_chunks", lambda *a, **k: [])
    monkeypatch.setattr("app.services.text2sql.hybrid_answer", lambda *a, **kw: calls.append(kw) or {})
    yield calls
    get_settings.cache_clear()


def _post(path: str, token: str | None = None, **body):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return TestClient(app).post(path, json={"question": "查询实验配方的盐雾平均值", "sources": [], "project_id": "p1", **body}, headers=headers)


def test_chat_hands_the_callers_owner_to_the_structured_route(seen):
    assert _post("/api/chat", "tok-alice").status_code == 200
    assert _post("/api/chat", "tok-bob").status_code == 200
    assert [c["owner_id"] for c in seen] == ["alice", "bob"]
    assert all(c["project_id"] == "p1" for c in seen)


def test_single_user_mode_is_the_default_owner_which_restricts_nothing(seen, monkeypatch):
    monkeypatch.delenv("FORMUMIND_MULTI_USER")
    assert _post("/api/chat").status_code == 200
    assert [c["owner_id"] for c in seen] == ["default"]


def test_a_client_cannot_name_its_own_owner(seen):
    assert _post("/api/chat", "tok-alice", owner_id="bob", _owner_id="bob").status_code == 200
    assert [c["owner_id"] for c in seen] == ["alice"]


def test_the_streaming_route_without_a_key_binds_the_owner_too(seen, monkeypatch):
    """No API key: ``chat_stream`` answers through ``chat(req)`` - the owner has to survive that hand-over."""
    monkeypatch.setattr(type(get_settings()), "get_active_api_key", lambda self: "")
    resp = _post("/api/chat/stream", "tok-alice")
    assert resp.status_code == 200
    assert [c["owner_id"] for c in seen] == ["alice"]


def test_the_streaming_plan_uses_the_owner_bound_on_the_request(monkeypatch):
    from app.domain.schemas import Evidence

    ev = Evidence(source="kb", identifier="kb:A", title="文献A", snippet="摘要内容用于测试。", relevance=0.9)
    monkeypatch.setattr(chat_api, "_augment_with_kb", lambda q, s, **kw: (list(s) + [ev], 1, None, None))

    class Store:
        def ingest(self, sources): ...
        def query(self, q, k=0): return [ev]

    import app.services.chat_clarify as clarify
    import app.services.chat_context as context
    import app.services.connectors_builtin as connectors
    import app.services.rag as rag

    monkeypatch.setattr(rag, "build_store", lambda: Store())
    monkeypatch.setattr(context, "rewrite_query", lambda q, h, ce=None, **kw: (q, ""))
    monkeypatch.setattr(context, "trim_history", lambda h, **kw: [])
    monkeypatch.setattr(clarify, "detect_clarification", lambda *a, **kw: None)
    monkeypatch.setattr(connectors, "gather_connector_evidence", lambda *a, **kw: [])
    calls: list[dict] = []
    monkeypatch.setattr("app.services.text2sql.hybrid_answer", lambda *a, **kw: calls.append(kw) or {})
    settings = SimpleNamespace(chat_rerank_candidates=20, chat_rerank_top_k=20, chat_skills_runtime_enabled=False)

    req = chat_api.ChatRequestValidated(question="水性环氧底漆的盐雾性能如何？", sources=[], project_id="p1")
    req._owner_id = "alice"
    chat_api._stream_answer_plan(req, settings)
    chat_api._stream_answer_plan(chat_api.ChatRequestValidated(question="q", sources=[]), settings)  # an internal call: no identity
    assert [c["owner_id"] for c in calls] == ["alice", None]


def test_the_owner_never_appears_in_the_request_schema():
    """Private: it is not a field a client can fill in, and not part of the API contract."""
    assert "_owner_id" not in chat_api.ChatRequestValidated.model_fields
    assert "owner_id" not in chat_api.ChatRequestValidated.model_fields
    assert "_owner_id" not in json.dumps(chat_api.ChatRequestValidated.model_json_schema())

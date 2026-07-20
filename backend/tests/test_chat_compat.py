"""Chat P0 — backward compatibility."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.domain.schemas import Evidence


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client() -> TestClient:
    from app.main import app

    return TestClient(app)


def test_chat_legacy_payload(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm.answer_question",
        lambda q, sources, domain=None, **kw: ("测试答案", sources[:1] or []),
    )
    monkeypatch.setattr("app.services.kb_index.search_chunks", lambda q, k=6, **_: [])
    resp = _client().post("/api/chat", json={"question": "你好", "sources": []})
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"]
    assert "structured" not in data or data.get("structured") is None


def test_chat_answer_never_empty(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm.answer_question",
        lambda *a, **k: ("", []),
    )
    monkeypatch.setattr("app.services.kb_index.search_chunks", lambda *a, **k: [])
    resp = _client().post("/api/chat", json={"question": "q", "sources": []})
    assert resp.status_code == 200
    assert resp.json()["answer"]

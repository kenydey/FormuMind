"""Chat P0-2 — structured answers."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.domain.chat_schemas import StructuredAnswer, StructuredAnswerResponse
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


def test_structured_response(monkeypatch):
    ev = Evidence(
        source="patent",
        identifier="kb:s1#c2",
        title="防腐专利",
        snippet="磷酸锌 15 wt%",
        relevance=0.9,
    )
    fake = StructuredAnswerResponse(
        answer=StructuredAnswer(
            summary="磷酸锌约 15 wt%。",
            key_findings=["实施例 1：15 wt%"],
            formulation_hints=[
                {
                    "ingredient": "磷酸锌",
                    "role": "防锈颜料",
                    "typical_range": "10-20 wt%",
                    "evidence_ref": "kb:s1#c2",
                }
            ],
        )
    )

    monkeypatch.setattr("app.services.kb_index.search_chunks", lambda *a, **k: [ev])
    monkeypatch.setattr(
        "app.services.chat_structured.complete_structured",
        lambda *a, **k: (fake, None),
    )

    resp = _client().post(
        "/api/chat",
        json={"question": "磷酸锌添加量", "sources": [ev.model_dump()], "response_format": "structured"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["structured"]["summary"] == data["answer"]
    assert data["structured"]["formulation_hints"][0]["evidence_ref"] == "kb:s1#c2"


def test_structured_fallback(monkeypatch):
    ev = Evidence(
        source="local",
        identifier="kb:x#c0",
        title="t",
        snippet="s",
        relevance=0.5,
    )
    monkeypatch.setattr("app.services.kb_index.search_chunks", lambda *a, **k: [ev])
    monkeypatch.setattr(
        "app.services.chat_structured.complete_structured",
        lambda *a, **k: (None, "fail"),
    )
    monkeypatch.setattr(
        "app.api.chat.answer_question",
        lambda *a, **k: ("markdown 答案", [ev]),
    )
    resp = _client().post(
        "/api/chat",
        json={"question": "q", "sources": [ev.model_dump()], "response_format": "structured"},
    )
    assert resp.status_code == 200
    assert resp.json()["answer"] == "markdown 答案"
    assert resp.json().get("structured") is None

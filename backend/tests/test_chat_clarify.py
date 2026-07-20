"""Chat P0-3 — soft clarification."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.domain.chat_schemas import ClarifiedEntity
from app.services.chat_clarify import detect_clarification


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_CHAT_CLARIFICATION_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_detect_waterborne_ambiguity():
    opt = detect_clarification("水性环氧体系如何设计？", [], [])
    assert opt is not None
    assert opt.ambiguous_term == "水性"
    assert len(opt.possible_meanings) >= 2


def test_clarified_term_skips_repeat():
    opt = detect_clarification(
        "水性底漆如何设计？",
        [],
        [ClarifiedEntity(term="水性", resolved="waterborne acrylic emulsion")],
    )
    assert opt is None


def test_chat_soft_clarify_non_blocking(monkeypatch):
    from app.main import app

    monkeypatch.setattr(
        "app.services.llm.answer_question",
        lambda *a, **k: ("按水乳液理解：PVC 约 35%。", []),
    )
    monkeypatch.setattr("app.services.kb_index.search_chunks", lambda *a, **k: [])
    resp = TestClient(app).post(
        "/api/chat",
        json={"question": "水性环氧怎么样？", "sources": []},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"]
    assert data.get("clarification") is not None

"""Chat P0-1 — multi-turn query rewrite."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.domain.chat_schemas import ChatTurn
from app.domain.schemas import Evidence
from app.services.chat_context import rewrite_query, trim_history


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_rewrite_followup_with_history(monkeypatch):
    monkeypatch.setenv("FORMUMIND_CHAT_MULTI_TURN_ENABLED", "true")
    get_settings.cache_clear()
    history = [
        ChatTurn(role="user", content="磷酸锌在环氧底漆中的添加量是多少？"),
        ChatTurn(
            role="assistant",
            content="实施例中磷酸锌约 15 wt%。",
            citations=[
                Evidence(
                    source="patent",
                    identifier="kb:s1#c0",
                    title="防腐专利",
                    snippet="磷酸锌 15 wt%",
                    relevance=0.9,
                )
            ],
        ),
    ]
    _q, rewritten = rewrite_query("那它的耐盐雾表现呢？", history)
    assert rewritten
    assert "磷酸锌" in rewritten or "防腐" in rewritten


def test_rewrite_disabled(monkeypatch):
    monkeypatch.setenv("FORMUMIND_CHAT_MULTI_TURN_ENABLED", "false")
    get_settings.cache_clear()
    history = [ChatTurn(role="user", content="磷酸锌添加量")]
    q, rewritten = rewrite_query("那耐盐雾呢", history)
    assert q == "那耐盐雾呢"
    assert rewritten is None


def test_trim_history():
    turns = [ChatTurn(role="user", content=str(i)) for i in range(20)]
    trimmed = trim_history(turns, max_turns=12)
    assert len(trimmed) == 12
    assert trimmed[0].content == "8"

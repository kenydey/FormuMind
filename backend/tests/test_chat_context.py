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


def _hist(*texts: str) -> list:
    return [ChatTurn(role="user", content=t) for t in texts]


def test_anaphora_substitutes_pronoun(monkeypatch):
    """P2: '它的X' → '磷酸锌的X'，而不仅是前置词条。"""
    monkeypatch.setenv("FORMUMIND_CHAT_MULTI_TURN_ENABLED", "true")
    get_settings.cache_clear()
    history = _hist("磷酸锌在环氧底漆中的添加量是多少？")
    q, rewritten = rewrite_query("它的耐盐雾表现呢？", history)
    assert rewritten is not None
    assert "磷酸锌的耐盐雾" in rewritten


def test_anaphora_ignores_yinggai(monkeypatch):
    """P2: '应该'中的'该'不是代词，不替换。"""
    monkeypatch.setenv("FORMUMIND_CHAT_MULTI_TURN_ENABLED", "true")
    get_settings.cache_clear()
    history = _hist("磷酸锌的添加量？")
    q, rewritten = rewrite_query("应该加多少？", history)
    assert rewritten is not None
    assert "磷酸锌加多少" not in rewritten  # "应该"保持完整


def test_anaphora_qianhouzhe(monkeypatch):
    """P2: 前者/后者按提及顺序消解。"""
    monkeypatch.setenv("FORMUMIND_CHAT_MULTI_TURN_ENABLED", "true")
    get_settings.cache_clear()
    history = _hist("比较磷酸锌和环氧树脂的防锈性能")
    q, rewritten = rewrite_query("前者的添加量呢？", history)
    assert rewritten is not None
    assert "磷酸锌的添加量" in rewritten


def test_v7_anaphora_no_false_positives():
    """v7 问答-1: 其实/极其/此时/此前/这个时候 不应被消解为实体。"""
    from app.services.chat_context import _ANAPHORA_RE

    # 这些不应匹配（返回 None 表示无代词可消解）
    for text in ["其实它的耐盐雾如何？", "极其重要的参数有哪些？", "此前的实验数据呢？", "这个时候该加多少？", "其次要考虑什么？"]:
        # "其实它的耐盐雾如何" 中的"它"是真代词，但"其实"的"其"不应先被匹配
        m = _ANAPHORA_RE.search(text)
        if m:
            # 如果匹配，确保不是误匹配的"其/此/这个"
            assert m.group(0) not in ("其",), f"{text!r} 误匹配 {m.group(0)!r}"
    # "其实" 的"其"不应匹配
    assert _ANAPHORA_RE.search("其实") is None
    assert _ANAPHORA_RE.search("极其") is None
    assert _ANAPHORA_RE.search("此时") is None
    assert _ANAPHORA_RE.search("此前") is None
    # 真代词仍应匹配
    assert _ANAPHORA_RE.search("它的耐盐雾如何") is not None
    assert _ANAPHORA_RE.search("该配方的") is not None

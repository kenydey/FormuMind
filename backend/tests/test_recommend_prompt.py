"""Tests for recommend prompt tuning (Phase B F-1b)."""
from __future__ import annotations

from app.services.llm import _recommend_system_prompt


def test_recommend_prompt_allows_blank_cas_and_requires_zh_name():
    prompt = _recommend_system_prompt()
    assert "leave blank" in prompt.lower() or "blank if uncertain" in prompt.lower()
    assert "zh_name" in prompt

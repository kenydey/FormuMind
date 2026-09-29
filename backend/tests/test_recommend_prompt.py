"""Tests for recommend prompt tuning (Phase B F-1b)."""
from __future__ import annotations

from app.services.llm import _recommend_system_prompt


def test_recommend_prompt_allows_blank_cas_and_requires_zh_name():
    prompt = _recommend_system_prompt()
    assert "leave blank" in prompt.lower() or "blank if uncertain" in prompt.lower()
    assert "zh_name" in prompt


def test_recommend_prompt_forbids_unrequested_ingredients():
    """A-4: 推荐 prompt 必须显式约束“不新增未请求成分”（用户明确偏好）。"""
    prompt = _recommend_system_prompt()
    lowered = prompt.lower()
    assert "do not add ingredients the user did not ask for" in lowered
    assert "unrequested ingredient is a defect" in lowered


def test_alternatives_prompt_forbids_new_material_additions():
    """A-4: 替代料 prompt 只许替换当前材料，不许提议新增材料。"""
    from app.services.llm_alternatives import _alternatives_prompt

    prompt = _alternatives_prompt("环氧树脂", "resin", 3)
    lowered = prompt.lower()
    assert "current material: 环氧树脂" in lowered
    assert "do not propose adding extra new materials" in lowered
    # 旧约束仍在（提取重构未丢内容）
    assert "do not invent cas numbers" in lowered
    assert "do not invent smiles" in lowered

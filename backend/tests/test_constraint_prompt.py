"""Tests for normalize_constraints and constraint prompt injection."""
from __future__ import annotations

from app.domain.project_spec import normalize_constraints
from app.domain.schemas import ProductDomain, Requirement
from app.services.llm import _constraints_prompt_block, _recommend_user_prompt


def test_normalize_constraints_merges_legacy_and_custom():
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        voc_limit_gpl=420,
        cure_temperature_c=80,
        constraints={"自定义约束": 12.5},
    )
    merged = normalize_constraints(req)
    assert merged["VOC 上限"] == 420
    assert merged["固化温度上限"] == 80
    assert merged["自定义约束"] == 12.5


def test_constraints_in_recommend_prompt():
    req = Requirement(
        domain=ProductDomain.degreaser,
        constraints={"pH 范围上限": 13},
    )
    block = _constraints_prompt_block(req)
    assert "工艺约束" in block
    assert "pH 范围上限" in block

    prompt = _recommend_user_prompt(req, [], [], 2)
    assert "pH 范围上限" in prompt

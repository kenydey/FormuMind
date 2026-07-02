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
        constraint_values={"自定义约束": 12.5},
    )
    merged = normalize_constraints(req)
    assert merged["VOC 上限"] == 420
    assert merged["固化温度上限"] == 80
    assert merged["自定义约束"] == 12.5


def test_migrate_legacy_constraints_field():
    req = Requirement(
        domain=ProductDomain.degreaser,
        constraints={"旧字段约束": 99.0},
    )
    assert "constraints" not in req.model_dump()
    assert req.constraint_values["旧字段约束"] == 99.0


def test_constraints_in_recommend_prompt():
    req = Requirement(
        domain=ProductDomain.degreaser,
        constraint_values={"pH 范围上限": 13},
    )
    block = _constraints_prompt_block(req)
    assert "pH 范围上限" in block

    prompt = _recommend_user_prompt(req, [], [], 2)
    assert "pH 范围上限" in prompt
    assert "Process constraints" in prompt

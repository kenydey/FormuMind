"""Unit tests for domain-adaptive formulation skill hints + aliases."""
from __future__ import annotations

from app.resources.formulation_skills import (
    DEFAULT_SEARCH_HINT,
    get_formulation_skill,
    list_formulation_skills,
    resolve_search_hint,
)


def test_formula_recommend_replaces_silane_playbook():
    ids = {s["id"] for s in list_formulation_skills()}
    assert "formula_recommend" in ids
    assert "silane_recommend" not in ids
    skill = get_formulation_skill("formula_recommend")
    assert skill is not None
    assert skill["title"] == "配方推荐"
    assert skill["presets"].get("search_hint_mode") == "domain"
    assert "硅烷" not in (skill["presets"].get("search_hint") or "")


def test_silane_alias_resolves_to_formula_recommend():
    aliased = get_formulation_skill("silane_recommend")
    current = get_formulation_skill("formula_recommend")
    assert aliased is not None and current is not None
    assert aliased["id"] == current["id"] == "formula_recommend"


def test_resolve_search_hint_domain_adaptive():
    presets = {"search_hint_mode": "domain"}
    assert resolve_search_hint(presets, domain="degreaser") == "脱脂剂 表面活性剂 清洗"
    assert "硅烷" not in (resolve_search_hint(presets, domain="anticorrosion_coating") or "")
    assert resolve_search_hint(presets, domain=None) == DEFAULT_SEARCH_HINT
    lit = resolve_search_hint({"search_hint_mode": "domain_literature"}, domain="surface_treatment")
    assert lit == "表面处理 转化膜 附着力 综述"

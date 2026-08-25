"""Tests for the formulation-system knowledge base + matching."""
from __future__ import annotations

from app.domain.formulation_systems import (
    CORROSION_GRADES,
    FORMULATION_SYSTEMS,
    build_system_prompt_block,
    match_grade,
    match_systems,
)


def _ids(text: str) -> set[str]:
    return {s.id for s in match_systems(text)}


# ── 体系匹配 ────────────────────────────────────────────────────────────────


def test_empty_returns_nothing():
    assert match_systems("") == []
    assert match_systems(None) == []  # type: ignore[arg-type]
    assert match_grade("") is None


def test_organic_emulsion():
    assert "organic_emulsion" in _ids("含聚合物/树脂的乳液型镁合金钝化剂")


def test_long_keyword_wins_over_generic():
    # 「铁系磷化」应命中 iron_phosphate，不命中 zinc_phosphate 的泛化「磷化」
    ids = _ids("铁系磷化")
    assert "iron_phosphate" in ids
    assert "zinc_phosphate" not in ids


def test_generic_phosphating_defaults_to_zinc():
    # 泛化「磷化转化膜」（无前缀）默认 zinc_phosphate
    assert "zinc_phosphate" in _ids("磷化转化膜")


def test_multi_match_chrome_free_emulsion():
    # 无铬 + 乳液 两个体系同时命中
    ids = _ids("无铬乳液型钝化")
    assert "chrome_free" in ids
    assert "organic_emulsion" in ids


def test_autodeposition():
    assert "autodeposition" in _ids("自沉积型涂料")


def test_anodizing_and_aluminum_line():
    assert "anodizing" in _ids("阳极氧化")
    assert "aluminum_line" in _ids("铝材前处理专线")


def test_dewaxing_and_sealing():
    assert "dewaxing" in _ids("除蜡剂")
    assert "passivation_sealing" in _ids("钝化封闭二合一")


def test_anticorr_systems():
    assert "zinc_rich_primer" in _ids("环氧富锌底漆")
    assert "mio_intermediate" in _ids("云母氧化铁中间漆")
    assert "pu_topcoat" in _ids("聚氨酯面漆")
    assert "high_build" in _ids("厚浆型环氧")
    assert "solvent_free" in _ids("无溶剂型涂料")


def test_unknown_returns_nothing():
    assert match_systems("xyzzy123") == []


# ── 防腐蚀等级匹配 ──────────────────────────────────────────────────────────


def test_grade_c5_chinese():
    g = match_grade("C5级重防腐涂料")
    assert g is not None and g.id == "C5"


def test_grade_c5_m():
    g = match_grade("C5-M 海洋环境")
    assert g is not None and g.id == "C5"


def test_grade_iso12944_c4():
    g = match_grade("ISO 12944 C4 钢结构防腐")
    assert g is not None and g.id == "C4"


def test_grade_bare_token():
    g = match_grade("C5")
    assert g is not None and g.id == "C5"


def test_grade_word_boundary_blocks_partial():
    # "c50" 不是 C5（词边界阻止）
    assert match_grade("c50 配方") is None


def test_grade_marine_keyword():
    g = match_grade("海洋级重防腐")
    assert g is not None and g.id == "C5"


def test_corrosion_grades_have_ranges():
    assert CORROSION_GRADES["C3"].salt_spray_hours == (240.0, 480.0)
    assert CORROSION_GRADES["C5"].salt_spray_hours == (720.0, 1440.0)


# ── prompt 块构建 ────────────────────────────────────────────────────────────


def test_build_block_autodeposition_detail():
    block = build_system_prompt_block("自沉积型")
    assert "Formulation-system requirements" in block
    assert "Autodeposition" in block
    assert "pH 2-4" in block
    assert "Fe3+" in block
    assert "epoxy:acrylic" in block
    assert "150-170°C" in block


def test_build_block_includes_grade():
    block = build_system_prompt_block("C5级")
    assert "Corrosion grade C5" in block
    assert "720-1440h" in block


def test_build_block_system_plus_grade():
    block = build_system_prompt_block("C5级无铬钝化")
    assert "chrome_free" in block or "Chrome-free" in block
    assert "Corrosion grade C5" in block


def test_build_block_empty():
    assert build_system_prompt_block("") == ""
    assert build_system_prompt_block("xyzzy123") == ""


def test_knowledge_base_completeness():
    # 知识库应包含 7 组 26 个体系 + 6 个等级
    assert len(FORMULATION_SYSTEMS) == 26
    assert len(CORROSION_GRADES) == 6


def test_p1_known_system_returns_hard_block():
    from app.domain.schemas import ProductDomain, Requirement
    from app.services.llm import _system_prompt_block

    req = Requirement(domain=ProductDomain.surface_treatment, product_type="自沉积型涂料")
    block = _system_prompt_block(req)
    assert "HARD constraints" in block
    assert "INFER" not in block


def test_p1_unknown_system_infers_constraints():
    from app.domain.schemas import ProductDomain, Requirement
    from app.services.llm import _system_prompt_block

    req = Requirement(domain=ProductDomain.anticorrosion_coating, product_type="电子级环氧胶粘剂")
    block = _system_prompt_block(req)
    assert "INFER" in block
    assert "infer and state the system constraints" in block
    assert "Forbidden components" in block

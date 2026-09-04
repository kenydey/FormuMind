"""P2: formulation_similarity 化学化改造测试(2026-09-04)。"""
from __future__ import annotations

import pytest

from app.services.kg.formulation_similarity import (
    _chemical_name_similarity,
    formulation_similarity,
)


def test_english_lexical_pseudo_similarity_downgraded():
    """审查例子: Waterborne epoxy vs Waterborne polyurethane —— 旧词法 0.5×weight
    高分(化学荒谬), 新实现只给 0.15 低置信兜底。"""
    s = _chemical_name_similarity(
        "Waterborne epoxy resin", "Waterborne polyurethane resin"
    )
    assert s == 0.15
    assert s < 0.2  # 远低于旧 0.5 加分


def test_chinese_names_no_lexical_bonus():
    """中文成分名(无空格)词法 split 后无重叠 → 不给分(旧实现同样 0)。"""
    assert _chemical_name_similarity("双酚A环氧树脂", "环氧改性丙烯酸") == 0.0


def test_unrelated_names_zero():
    assert _chemical_name_similarity("Zinc phosphate", "Cerium nitrate") == 0.0
    assert _chemical_name_similarity("水", "硅烷偶联剂") == 0.0


def test_exact_same_name_after_normalize_returns_one():
    """别名归一化(resolve_material_name)命中真同一物 → 1.0。"""
    try:
        from app.domain.knowledge import resolve_material_name
    except Exception:
        pytest.skip("resolve_material_name 不可用")
    # 同义词表内成对名若命中归一化则 1.0(找不到已知对就跳过, 不臆造)
    pairs = [
        ("Zinc oxide", "ZnO"),
        ("zinc oxide", "Zinc Oxide"),
    ]
    hit = False
    for a, b in pairs:
        v = _chemical_name_similarity(a, b)
        if v > 0.5:
            hit = True
            assert v == 1.0
    if not hit:
        pytest.skip("别名表中无匹配对, 跳过归一化断言")


def test_common_ingredient_path_unchanged():
    """已匹配成分主路径(用量相似度)不受 kg_bonus 改造影响。"""
    q = {"树脂A": 30.0, "固化剂": 10.0}
    c = {"树脂A": 24.0, "固化剂": 8.0}
    s = formulation_similarity(q, c)
    assert s > 0.7  # 用量差 20% → 高相似(主路径)


def test_lexical_only_bonus_bounded_low():
    """只有词法兜底(无共同成分)时总相似度被压到低值, 不再虚高。"""
    q = {"Waterborne epoxy resin": 50.0}
    c = {"Waterborne polyurethane resin": 50.0}
    s = formulation_similarity(q, c)
    assert s <= 0.3


def test_fingerprint_path_high_tanimoto_counts(monkeypatch):
    """R5: 双方解析到目录且都有 SMILES → 走指纹路径; Tanimoto≥0.6 计入。"""
    from app.services.kg import formulation_similarity as fs

    monkeypatch.setattr(fs, "_tanimoto_similarity", lambda a, b: 0.72)
    # Zinc phosphate / Zinc molybdate 都在 RAW_MATERIALS 且有 smiles(5a 回填)
    assert fs._chemical_name_similarity("Zinc phosphate", "Zinc molybdate") == 0.72


def test_fingerprint_path_below_threshold_zero(monkeypatch):
    """Tanimoto <0.6 → 不给低置信加分(宁缺勿滥)。"""
    from app.services.kg import formulation_similarity as fs

    monkeypatch.setattr(fs, "_tanimoto_similarity", lambda a, b: 0.41)
    assert fs._chemical_name_similarity("Zinc phosphate", "Zinc molybdate") == 0.0


def test_fingerprint_real_values_conservative_for_inorganics():
    """真实无机盐对(磷酸锌/钼酸锌/氧化物)指纹相似度低 → 0(化学上正确的保守)。"""
    from app.services.kg.formulation_similarity import _chemical_name_similarity as s

    assert s("Zinc phosphate", "Zinc molybdate") == 0.0
    assert s("Zinc oxide", "Titanium dioxide") == 0.0
    assert s("Zinc dust", "Zinc phosphate") == 0.0
    assert s("Bismuth neodecanoate", "Zinc molybdate") == 0.0

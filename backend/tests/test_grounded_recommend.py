"""Tests for grounded recommend post-check (Sprint 2)."""
from __future__ import annotations

from app.domain.schemas import Evidence, ProductDomain, RecommendedFormula, RecommendedFormulaComponent
from app.services.grounded_recommend import ground_recommended_formulas


def _ev(ident: str, snippet: str) -> Evidence:
    return Evidence(
        source="patent",
        identifier=ident,
        title=ident,
        snippet=snippet,
        relevance=0.9,
    )


def test_ground_strict_drops_unknown_ingredient_by_default():
    """A-5: strict 默认开 — 低可信度成分被剔除（而非仅标记），剔除记入 warnings。"""
    formulas = [
        RecommendedFormula(
            name="Test A",
            domain=ProductDomain.anticorrosion_coating,
            components=[
                RecommendedFormulaComponent(name="Zinc phosphate", weight_pct=10.0),
                RecommendedFormulaComponent(name="Imaginaryium-X999", weight_pct=5.0),
            ],
        )
    ]
    evidence = [_ev("US123", "Zinc phosphate 10 wt% in epoxy primer")]
    out, warnings = ground_recommended_formulas(formulas, evidence)
    assert [c.name for c in out[0].components] == ["Zinc phosphate"]
    assert out[0].components[0].grounding_confidence == "high"
    assert any("Imaginaryium-X999" in w and "剔除" in w for w in warnings)
    assert any("已剔除低可信度成分" in w for w in out[0].warnings)


def test_ground_non_strict_marks_unknown_ingredient_low_confidence():
    """strict=False 保留旧行为：仅标记低可信度成分。"""
    formulas = [
        RecommendedFormula(
            name="Test A",
            domain=ProductDomain.anticorrosion_coating,
            components=[
                RecommendedFormulaComponent(name="Zinc phosphate", weight_pct=10.0),
                RecommendedFormulaComponent(name="Imaginaryium-X999", weight_pct=5.0),
            ],
        )
    ]
    evidence = [_ev("US123", "Zinc phosphate 10 wt% in epoxy primer")]
    out, warnings = ground_recommended_formulas(formulas, evidence, strict=False)
    assert out[0].components[0].grounding_confidence == "high"
    assert out[0].components[1].grounding_confidence == "low"
    assert warnings


def test_catalog_ingredient_gets_high_confidence_without_evidence():
    formulas = [
        RecommendedFormula(
            name="Test B",
            domain=ProductDomain.anticorrosion_coating,
            components=[
                RecommendedFormulaComponent(name="Bisphenol-A epoxy (DGEBA)", weight_pct=40.0),
            ],
        )
    ]
    out, _ = ground_recommended_formulas(formulas, [])
    assert out[0].components[0].grounding_confidence == "high"


def test_cas_in_evidence_yields_high_confidence():
    formulas = [
        RecommendedFormula(
            name="Test C",
            domain=ProductDomain.anticorrosion_coating,
            components=[
                RecommendedFormulaComponent(
                    name="Zinc phosphate",
                    cas_no="7779-90-0",
                    weight_pct=10.0,
                ),
            ],
        )
    ]
    evidence = [
        _ev("US9982145B2", "Zinc rich primer formulation with CAS 7779-90-0 inhibitor"),
    ]
    out, _ = ground_recommended_formulas(formulas, evidence)
    assert out[0].components[0].grounding_confidence == "high"


def test_weak_name_overlap_without_cas_is_low():
    formulas = [
        RecommendedFormula(
            name="Test D",
            domain=ProductDomain.anticorrosion_coating,
            components=[
                RecommendedFormulaComponent(name="Obscure additive QZX-42", weight_pct=1.0),
            ],
        )
    ]
    evidence = [_ev("US999", "General polymer coatings market report overview")]
    # strict 默认：唯一成分低可信度 → 整个配方被剔除。
    out, warnings = ground_recommended_formulas(formulas, evidence)
    assert out == []
    assert any("整个配方已剔除" in w for w in warnings)
    # strict=False：旧行为，仅标记。
    out, warnings = ground_recommended_formulas(formulas, evidence, strict=False)
    assert out[0].components[0].grounding_confidence == "low"
    assert warnings


def test_match_evidence_ids_per_evidence_not_global():
    """P1-4：单条证据匹配用该条目的 token 集合，而非全局并集。

    旧代码查 t in corpus（全部证据的 token 并集），导致任一 token 在语料
    任意处出现过，所有证据 id 都被挂成引用（假引用）。
    """
    from app.services.grounded_recommend import _evidence_corpus, _match_evidence_ids

    ev1 = _ev("CN123", "Epoxy resin cured with amine hardener for anticorrosion coating")
    ev2 = _ev("LIT456", "Alkaline degreaser for metal surface cleaning before coating")
    _, _, id_map, per_ev = _evidence_corpus([ev1, ev2])
    refs = _match_evidence_ids("Epoxy resin", per_ev, id_map)
    assert refs == ["CN123"], f"应只返回专利证据，实际: {refs}"
    # 反向：查除油剂只返回文献
    refs2 = _match_evidence_ids("degreaser", per_ev, id_map)
    assert refs2 == ["LIT456"], f"实际: {refs2}"

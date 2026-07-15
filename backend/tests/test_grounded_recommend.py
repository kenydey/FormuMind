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


def test_ground_marks_unknown_ingredient_low_confidence():
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

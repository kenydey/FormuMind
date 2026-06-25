"""Tests for formulation validation gate."""
from __future__ import annotations

from app.domain.formulation_gate import parse_llm_formulations, validate_formulations
from app.domain.schemas import Formulation, Ingredient, ProductDomain


def test_enrich_cas_from_knowledge():
    form = Formulation(
        name="test",
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[
            Ingredient(name="Bisphenol-A epoxy (DGEBA)", role="resin", weight_pct=50),
            Ingredient(name="Zinc phosphate", role="inhibitor", weight_pct=50),
        ],
    )
    enriched, warnings = validate_formulations([form])
    assert len(enriched) == 1
    cas_names = {i.name: i.cas_no for i in enriched[0].ingredients}
    assert cas_names["Bisphenol-A epoxy (DGEBA)"] == "1675-54-3"
    assert cas_names["Zinc phosphate"] == "7779-90-0"
    assert not any("no CAS" in w for w in warnings)


def test_parse_llm_invalid_json():
    forms, warnings = parse_llm_formulations({"formulations": [{"name": 123}]})
    assert forms == []
    assert warnings


def test_parse_llm_valid_list():
    payload = {
        "formulations": [
            {
                "name": "Variant A",
                "domain": "anticorrosion_coating",
                "ingredients": [
                    {"name": "Zinc phosphate", "role": "inhibitor", "weight_pct": 10},
                    {"name": "Bisphenol-A epoxy (DGEBA)", "role": "resin", "weight_pct": 90},
                ],
            }
        ]
    }
    forms, warnings = parse_llm_formulations(payload)
    assert len(forms) == 1
    assert forms[0].ingredients[0].cas_no == "7779-90-0"

"""Tests for formulation validation gate."""
from __future__ import annotations

from unittest.mock import patch

from app.domain.formulation_gate import (
    enrich_component,
    enrich_ingredient,
    parse_llm_formulations,
    validate_formulations,
)
from app.domain.knowledge import resolve_material_name
from app.domain.schemas import Formulation, Ingredient, ProductDomain, RecommendedFormulaComponent, Requirement


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


def test_enrich_ingredient_fills_zh_from_catalog():
    ing = enrich_ingredient(
        Ingredient(name="Hexafluorozirconic acid", role="active", weight_pct=1.0)
    )
    assert ing.cas_no == "12021-95-3"
    assert ing.zh_name == "六氟锆酸"


def test_enrich_ingredient_lookup_chemical_fallback():
    ing = Ingredient(name="Unknown chemical XYZ", role="additive", weight_pct=1.0)
    fake = {
        "query": "Unknown chemical XYZ",
        "cas": "123-45-6",
        "zh_name": "未知化学品",
        "formula": "C2H6",
        "smiles": "CC",
        "molar_mass": 30.0,
        "found": True,
        "source": "mock",
    }
    with patch("app.services.chemical_lookup.lookup_chemical", return_value=fake):
        out = enrich_ingredient(ing)
    assert out.cas_no == "123-45-6"
    assert out.zh_name == "未知化学品"
    assert out.smiles == "CC"


def test_resolve_trade_alias_epon_828():
    assert resolve_material_name("Epon 828") == "Bisphenol-A epoxy (DGEBA)"
    ing = enrich_ingredient(Ingredient(name="Epon 828", role="resin", weight_pct=50))
    assert ing.cas_no == "1675-54-3"
    assert ing.zh_name == "双酚A型环氧树脂"


def test_enrich_rejects_invalid_cas_checksum():
    ing = Ingredient(name="Unknown chemical XYZ", role="additive", weight_pct=1.0, cas_no="12-34-5")
    fake = {
        "query": "Unknown chemical XYZ",
        "cas": "123-45-6",
        "zh_name": "未知化学品",
        "formula": "C2H6",
        "smiles": "CC",
        "molar_mass": 30.0,
        "found": True,
        "source": "mock",
    }
    with patch("app.services.chemical_lookup.lookup_chemical", return_value=fake):
        enriched, warnings = validate_formulations(
            [
                Formulation(
                    name="t",
                    domain=ProductDomain.anticorrosion_coating,
                    ingredients=[ing],
                )
            ]
        )
    assert enriched[0].ingredients[0].cas_no == "123-45-6"
    assert any("校验失败" in w for w in warnings)


def test_validate_formulations_requirement_voc_warning():
    req = Requirement(domain=ProductDomain.anticorrosion_coating, voc_limit_gpl=250.0)
    form = Formulation(
        name="high voc",
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[
            Ingredient(name="Bisphenol-A epoxy (DGEBA)", role="resin", weight_pct=50),
            Ingredient(name="Zinc phosphate", role="inhibitor", weight_pct=50),
        ],
        predicted={"voc_gpl": 400.0},
    )
    _, warnings = validate_formulations([form], req=req)
    assert any("VOC" in w for w in warnings)


def test_enrich_component_full_lookup_fallback():
    comp = RecommendedFormulaComponent(name="Unknown chemical XYZ", weight_pct=1.0)
    fake = {
        "query": "Unknown chemical XYZ",
        "cas": "123-45-6",
        "zh_name": "未知化学品",
        "formula": "C2H6",
        "smiles": "CC",
        "molar_mass": 30.0,
        "found": True,
        "source": "mock",
    }
    with patch("app.services.chemical_lookup.lookup_chemical", return_value=fake):
        out = enrich_component(comp)
    assert out.cas_no == "123-45-6"
    assert out.zh_name == "未知化学品"
    assert out.smiles == "CC"
    assert out.mf == "C2H6"
    assert out.molar_mass == 30.0


def test_ipda_cas_from_catalog_without_gateway():
    ing = enrich_ingredient(Ingredient(name="Isophorone diamine (IPDA)", role="hardener", weight_pct=20))
    assert ing.cas_no == "2855-13-2"
    assert ing.zh_name == "异佛尔酮二胺"

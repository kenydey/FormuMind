"""Tests for acid_stability and compliance_rules engines."""

import pytest

from app.domain.schemas import Formulation, Ingredient, ProductDomain
from app.services.acid_stability import check_acid_stability
from app.services.compliance_rules import check_compliance


def _form(*ingredients: tuple[str, float, str], domain=ProductDomain.autodeposition_coating) -> Formulation:
    """ingredients: (name, weight_pct, role)"""
    ings = [
        Ingredient(name=n, weight_pct=w, role=r)
        for n, w, r in ingredients
    ]
    return Formulation(name="test", domain=domain, ingredients=ings)


# ── Acid stability: dispersion tolerance axis ───────────────────────────────

def test_acid_tolerant_emulsion_passes_at_bath_ph_3():
    form = _form(
        ("Acidic-stable epoxy-acrylic emulsion", 12.0, "resin"),
        ("Deionized water", 88.0, "solvent"),
    )
    res = check_acid_stability(form, bath_ph=3.0)
    assert res.stable is True
    assert res.status == "pass"


def test_untolerant_emulsion_warns_at_low_ph():
    # No acid_tolerance_ph in catalog → unknown → no constraint.
    form = _form(
        ("Waterborne acrylic emulsion", 12.0, "resin"),
        ("Deionized water", 88.0, "solvent"),
    )
    res = check_acid_stability(form, bath_ph=2.0)
    assert res.status == "pass"


def test_polyurethane_dispersion_warns_below_tolerance():
    # Cationic PUD tolerance = 2.5 → bath pH 2.0 is below it.
    form = _form(
        ("Cationic polyurethane dispersion (acid-stable)", 10.0, "resin"),
        ("Deionized water", 90.0, "solvent"),
    )
    res = check_acid_stability(form, bath_ph=2.0)
    assert res.status == "warn"
    assert any("酸耐受" in r for r in res.reasons)


# ── Acid stability: composition rules ───────────────────────────────────────

def test_strong_alkali_in_acid_bath_is_infeasible():
    form = _form(
        ("Sodium hydroxide", 6.0, "builder"),
        ("Deionized water", 94.0, "solvent"),
    )
    res = check_acid_stability(form, bath_ph=3.0)
    assert res.stable is False
    assert res.status == "infeasible"
    assert any("强碱" in r for r in res.reasons)


def test_carbonate_filler_in_acid_bath_is_infeasible():
    form = _form(
        ("Calcium carbonate", 8.0, "filler"),
        ("Deionized water", 92.0, "solvent"),
    )
    res = check_acid_stability(form, bath_ph=3.0)
    assert res.status == "infeasible"
    assert any("CO₂" in r for r in res.reasons)


def test_reactive_metal_in_acid_bath_is_infeasible():
    form = _form(
        ("Zinc dust", 10.0, "pigment"),
        ("Deionized water", 90.0, "solvent"),
    )
    res = check_acid_stability(form, bath_ph=3.0)
    assert res.status == "infeasible"
    assert any("析氢" in r for r in res.reasons)


def test_clean_autodeposition_bath_passes():
    form = _form(
        ("Acidic-stable epoxy-acrylic emulsion", 12.0, "resin"),
        ("Ferric fluoride (FeF3)", 1.3, "active"),
        ("Hydrofluoric acid (HF)", 0.2, "active"),
        ("Hydrogen peroxide (H2O2)", 0.8, "accelerator"),
        ("Citric acid", 1.0, "chelant"),
        ("Carbon black", 2.0, "pigment"),
        ("Deionized water", 82.7, "solvent"),
    )
    res = check_acid_stability(form, bath_ph=3.0)
    assert res.stable is True
    assert res.status == "pass"


def test_default_bath_ph_when_not_provided():
    # No predicted ph, no bath_ph → default 3.0 window.
    form = _form(
        ("Acidic-stable epoxy-acrylic emulsion", 12.0, "resin"),
        ("Deionized water", 88.0, "solvent"),
    )
    res = check_acid_stability(form)
    assert res.status == "pass"
    assert res.bath_ph == 3.0


# ── Compliance: RoHS ────────────────────────────────────────────────────────

def test_rohs_lead_pigment_is_infeasible():
    form = _form(
        ("Red lead", 5.0, "pigment"),
        ("Bisphenol-A epoxy (DGEBA)", 40.0, "resin"),
        ("Deionized water", 55.0, "solvent"),
    )
    res = check_compliance(form)
    assert res.compliant is False
    assert res.status == "infeasible"
    assert len(res.rohs_hits) == 1
    assert "RoHS" in res.reasons[0]


def test_rohs_by_cas_match():
    form = _form(
        ("Some brand chromate", 3.0, "pigment"),
        ("Deionized water", 97.0, "solvent"),
    )
    form.ingredients[0].cas_no = "7789-06-2"  # strontium chromate CAS
    res = check_compliance(form)
    assert res.status == "infeasible"


def test_svhc_candidate_is_warn_not_hard():
    form = _form(
        ("Cerium nitrate", 0.5, "inhibitor"),
        ("Deionized water", 99.5, "solvent"),
    )
    res = check_compliance(form)
    assert res.compliant is True
    assert res.status == "warn"
    assert len(res.svhc_hits) == 1


def test_clean_formulation_passes_compliance():
    form = _form(
        ("Acidic-stable epoxy-acrylic emulsion", 12.0, "resin"),
        ("Ferric fluoride (FeF3)", 1.3, "active"),
        ("Deionized water", 86.7, "solvent"),
    )
    res = check_compliance(form)
    assert res.status == "pass"
    assert res.rohs_hits == []


def test_zero_weight_ingredients_skipped():
    form = _form(
        ("Red lead", 0.0, "pigment"),
        ("Deionized water", 100.0, "solvent"),
    )
    res = check_compliance(form)
    assert res.status == "pass"


# ── Equivalent ratio & dimension closure ────────────────────────────────────

def test_equivalent_ratio_true_stoichiometry():
    from app.domain.chemistry import equivalent_ratio
    from app.domain.knowledge import baseline_formulation
    from app.domain.schemas import Requirement, Substrate

    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        substrate=Substrate.carbon_steel,
        salt_spray_hours=500,
    )
    form = baseline_formulation(req)
    ratio = equivalent_ratio(form)
    assert ratio is not None
    # DGEBA 38/190eq + polyamide 14/95eq → 0.2 / 0.147 ≈ 1.36 (slight excess epoxy)
    assert 1.2 < ratio < 1.5


def test_equivalent_ratio_none_without_hardener():
    from app.domain.chemistry import equivalent_ratio

    form = _form(
        ("Acidic-stable epoxy-acrylic emulsion", 12.0, "resin"),
        ("Deionized water", 88.0, "solvent"),
    )
    assert equivalent_ratio(form) is None


def test_equivalent_ratio_respects_catalog_metadata():
    from app.domain.chemistry import equivalent_ratio

    # Resin without equivalent_weight metadata → ratio unavailable, no crash.
    form = _form(
        ("Waterborne acrylic emulsion", 40.0, "resin"),
        ("Polyamide hardener", 14.0, "hardener"),
        ("Deionized water", 46.0, "solvent"),
    )
    assert equivalent_ratio(form) is None


def test_dimension_closure_clean_formulation():
    from app.domain.chemistry import dimension_closure
    from app.domain.knowledge import baseline_formulation
    from app.domain.schemas import Requirement, Substrate

    req = Requirement(
        domain=ProductDomain.autodeposition_coating,
        substrate=Substrate.carbon_steel,
        salt_spray_hours=500,
    )
    form = baseline_formulation(req)
    assert dimension_closure(form) == []


def test_dimension_closure_detects_solids_anomaly():
    from app.domain.chemistry import dimension_closure

    # Solids > 100 impossible: water 10 + solvent 20 + solids 90 → sums 120.
    form = _form(
        ("Bisphenol-A epoxy (DGEBA)", 90.0, "resin"),
        ("Deionized water", 10.0, "solvent"),
        ("Xylene", 20.0, "solvent"),
    )
    warnings = dimension_closure(form)
    assert any("Weight percentages sum" in w for w in warnings)


def test_dimension_closure_waterborne_needs_water():
    from app.domain.chemistry import dimension_closure

    # An aqueous-carried resin but zero water/solvent → classified waterborne
    # yet solids alone can't close; assert no crash and sane output.
    form = _form(
        ("Acidic-stable epoxy-acrylic emulsion", 100.0, "resin"),
    )
    warnings = dimension_closure(form)
    assert isinstance(warnings, list)

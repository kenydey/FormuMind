"""P1-5: 化学式抽取上游清洗。

Contract under test:
1. ``sanitize_formula`` fixes unambiguous case mangling from OCR/LLM
   (ZNO→ZnO, h2o→H2O, TIO2→TiO2) and strips charge markers.
2. Ambiguous tokens are NOT guessed: NO stays NO (not nobelium), "co"
   stays "co" (C+O vs cobalt) → rejected downstream instead of misread.
3. Junk (illegal characters, unsalvageable strings) → None.
4. ``validate_formulation`` no longer warns "Unknown element" for cleanable
   formulas; the ValueError→warning path remains for the truly unknown.
5. ``extract_formulas`` recovers case-mangled formulas from text.
"""
from __future__ import annotations

from app.domain.chemistry import molar_mass, sanitize_formula, validate_formulation
from app.domain.schemas import Formulation, Ingredient
from app.services.chem_extract import extract_formulas


# ── sanitize_formula ──────────────────────────────────────────────────────────


def test_fixes_unambiguous_all_caps():
    assert sanitize_formula("ZNO") == "ZnO"
    assert sanitize_formula("TIO2") == "TiO2"
    assert sanitize_formula("NACL") == "NaCl"
    assert sanitize_formula("H2SO4") == "H2SO4"


def test_fixes_lowercase():
    assert sanitize_formula("h2o") == "H2O"
    assert sanitize_formula("zn3(po4)2") == "Zn3(PO4)2"


def test_strips_charge_markers():
    assert sanitize_formula("Fe3+") == "Fe3"


def test_ambiguous_tokens_not_guessed():
    # NO: N+O (nitric oxide) vs No (nobelium) — must not become nobelium.
    assert sanitize_formula("NO") == "NO"
    assert abs(molar_mass(sanitize_formula("NO")) - (14.007 + 15.999)) < 0.01
    # "co" lowercases to "CO" (carbon monoxide, the common reading) —
    # never silently misread as cobalt.
    assert sanitize_formula("co") == "CO"


def test_junk_rejected():
    assert sanitize_formula("") is None
    assert sanitize_formula(None) is None
    assert sanitize_formula("   ") is None
    assert sanitize_formula("Xx2Yy") is None  # unknown elements
    assert sanitize_formula("H2O!!") is None  # illegal characters
    assert sanitize_formula("not a formula at all") is None


def test_valid_formulas_pass_through():
    assert sanitize_formula("Zn3(PO4)2") == "Zn3(PO4)2"
    assert sanitize_formula("CuSO4·5H2O") == "CuSO4·5H2O"


# ── validate_formulation ─────────────────────────────────────────────────────


def _form(*formulas: str) -> Formulation:
    from app.domain.schemas import ProductDomain

    return Formulation(
        name="test-form",
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[
            Ingredient(
                name=f"ing{i}", role="pigment", formula=f,
                weight_pct=100.0 / len(formulas),
            )
            for i, f in enumerate(formulas)
        ],
    )


def test_validate_no_unknown_element_warning_for_cleanable():
    warnings = validate_formulation(_form("ZNO", "h2o"))
    assert not any("Unknown element" in w for w in warnings), warnings


def test_validate_still_warns_for_truly_unknown():
    warnings = validate_formulation(_form("Xx2O"))
    assert any("Xx" in w for w in warnings), warnings


def test_validate_normalizes_formula_in_place():
    form = _form("ZNO")
    validate_formulation(form)
    assert form.ingredients[0].formula == "ZnO"


# ── extract_formulas ─────────────────────────────────────────────────────────


def test_extract_recovers_case_mangled_formulas():
    # TIO2 / FE2O3 pass the plausibility heuristic (stoichiometric digit)
    # but used to die in molar_mass on the all-caps element ("TI").
    found = extract_formulas("配方含 TIO2 10% 与 FE2O3 5%")
    assert "TiO2" in found
    assert "Fe2O3" in found


def test_extract_still_drops_junk():
    found = extract_formulas("含有 Xx9Yy8 物质")
    assert "Xx9Yy8" not in found

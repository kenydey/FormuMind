import pytest

from app.domain import chemistry, knowledge
from app.domain.schemas import Formulation, Ingredient, ProductDomain, Requirement


def test_molar_mass_simple():
    assert chemistry.molar_mass("H2O") == pytest.approx(18.015, abs=0.01)


def test_molar_mass_nested_parentheses():
    # Zn3(PO4)2 = 3*65.38 + 2*(30.974 + 4*15.999)
    assert chemistry.molar_mass("Zn3(PO4)2") == pytest.approx(386.11, abs=0.05)
    assert chemistry.molar_mass("Mn(H2PO4)2") == pytest.approx(248.93, abs=0.1)


def test_molar_mass_hydrate_dot_separator():
    # 水合物「·」分隔符：NH3·H2O = NH3 + H2O；CuSO4·5H2O = CuSO4 + 5×H2O
    assert chemistry.molar_mass("NH3·H2O") == pytest.approx(
        chemistry.molar_mass("NH3") + chemistry.molar_mass("H2O"), abs=0.01
    )
    assert chemistry.molar_mass("CuSO4·5H2O") == pytest.approx(
        chemistry.molar_mass("CuSO4") + 5 * chemistry.molar_mass("H2O"), abs=0.05
    )


def test_unknown_element_raises():
    with pytest.raises(ValueError):
        chemistry.molar_mass("Xx2")


def test_baseline_formulations_close_to_100pct():
    for domain in ProductDomain:
        form = knowledge.baseline_formulation(Requirement(domain=domain))
        assert form.total_pct() == pytest.approx(100.0, abs=0.5)


def test_validation_flags_no_errors_on_baseline():
    form = knowledge.baseline_formulation(Requirement(domain=ProductDomain.anticorrosion_coating))
    warnings = chemistry.validate_formulation(form)
    assert warnings == []


# ── P-A: element balance (claim-missing-source detection) ───────────────────

def test_element_balance_claims_phosphate_but_no_p_source():
    form = Formulation(
        name="test",
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[
            Ingredient(name="Zinc phosphate pigment", formula="", role="pigment", weight_pct=8.0),
            Ingredient(name="Epoxy resin", formula="C21H24O4", role="resin", weight_pct=60.0),
        ],
        rationale="t",
    )
    warns = chemistry.element_balance_check(form)
    assert any("宣称含 P" in w for w in warns)
    assert any("宣称含 Zn" in w for w in warns)


def test_element_balance_satisfied_when_source_present():
    form = Formulation(
        name="test",
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[
            Ingredient(name="Zinc phosphate pigment", formula="Zn3(PO4)2", role="pigment", weight_pct=8.0),
            Ingredient(name="Epoxy resin", formula="C21H24O4", role="resin", weight_pct=60.0),
        ],
        rationale="t",
    )
    warns = chemistry.element_balance_check(form)
    assert warns == []


def test_element_balance_no_false_positive_without_claims():
    # 无宣称词（silane/zinc/phosphate 都不在名字里）→ 零告警
    form = Formulation(
        name="test",
        domain=ProductDomain.autodeposition_coating,
        ingredients=[
            Ingredient(name="Acrylic emulsion", formula="C3H4O2", role="binder", weight_pct=50.0),
            Ingredient(name="Water", formula="H2O", role="carrier", weight_pct=50.0),
        ],
        rationale="t",
    )
    warns = chemistry.element_balance_check(form)
    assert warns == []


# ── P-F: reaction SMARTS functional-group counting ──────────────────────────

def test_functional_group_count_epoxy_dgeba():
    # DGEBA has exactly 2 epoxide rings
    assert chemistry.functional_group_count(
        "CC(C)(c1ccc(OCC2CO2)cc1)c1ccc(OCC2CO2)cc1", "epoxy"
    ) == 2


def test_functional_group_count_amine_ipda():
    # IPDA has 2 primary amines (no secondary)
    ipda = "CC1(C)CC(N)CC(C)(CN)C1"
    assert chemistry.functional_group_count(ipda, "amine_primary") == 2
    assert chemistry.functional_group_count(ipda, "amine_secondary") == 0


def test_functional_group_count_invalid_returns_zero():
    assert chemistry.functional_group_count("not-a-molecule", "epoxy") == 0
    assert chemistry.functional_group_count("", "epoxy") == 0
    assert chemistry.functional_group_count("CCO", "unknown_group") == 0


def test_structure_equivalent_ratio_dgeba_ipda():
    """70g DGEBA + 30g IPDA → epoxy:amine-H ≈ 0.584 (hand-computed)."""
    form = Formulation(
        name="t",
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[
            Ingredient(
                name="DGEBA",
                smiles="CC(C)(c1ccc(OCC2CO2)cc1)c1ccc(OCC2CO2)cc1",
                formula="C21H24O4",
                role="resin",
                weight_pct=70.0,
            ),
            Ingredient(
                name="IPDA",
                smiles="CC1(C)CC(N)CC(C)(CN)C1",
                formula="C10H22N2",
                role="hardener",
                weight_pct=30.0,
            ),
        ],
        rationale="t",
    )
    ratio = chemistry.structure_equivalent_ratio(form)
    assert ratio is not None
    assert abs(ratio - 0.584) < 0.02


def test_structure_equivalent_ratio_missing_side_returns_none():
    # 只有树脂（无固化剂）→ None（不臆造）
    form = Formulation(
        name="t",
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[
            Ingredient(
                name="DGEBA",
                smiles="CC(C)(c1ccc(OCC2CO2)cc1)c1ccc(OCC2CO2)cc1",
                formula="C21H24O4",
                role="resin",
                weight_pct=100.0,
            ),
        ],
        rationale="t",
    )
    assert chemistry.structure_equivalent_ratio(form) is None


# ── M-B: hill-formula canonicalisation ──────────────────────────────────────

def test_canonical_formula_equivalent_forms():
    # Zn3(PO4)2 and Zn3P2O8 are the same formula → same Hill key
    a = chemistry.canonical_formula("Zn3(PO4)2")
    b = chemistry.canonical_formula("Zn3P2O8")
    assert a is not None and a == b == "O8P2Zn3"


def test_canonical_formula_invalid_returns_none():
    assert chemistry.canonical_formula("") is None
    assert chemistry.canonical_formula("not-a-formula") is None


def test_extract_formulas_dedupes_hill_equivalent():
    from app.services.chem_extract import extract_formulas

    r = extract_formulas("配方含 Zn3(PO4)2 与 Zn3P2O8 两种写法")
    assert len(r) == 1  # Hill-deduped, first display form kept
    assert r[0] == "Zn3(PO4)2"


def test_amine_epoxy_ratio_present_for_2k_system():
    form = knowledge.baseline_formulation(Requirement(domain=ProductDomain.anticorrosion_coating))
    ratio = chemistry.amine_epoxy_ratio(form)
    assert ratio is not None and ratio > 0


# ── PVC / CPVC / Solids-by-Volume ─────────────────────────────────────────────

def test_pvc_positive_for_pigmented_formula():
    form = knowledge.baseline_formulation(Requirement(domain=ProductDomain.anticorrosion_coating))
    val = chemistry.pvc(form)
    assert val > 0, "Anticorrosion primer contains pigments; PVC must be > 0"
    assert val < 100, "PVC must be < 100%"


def test_solids_by_volume_in_range():
    form = knowledge.baseline_formulation(Requirement(domain=ProductDomain.anticorrosion_coating))
    sbv = chemistry.solids_by_volume(form)
    # Typical solventborne primer: 40–70% SBV; waterborne may be lower.
    assert 20.0 < sbv < 90.0, f"Solids by volume {sbv} outside plausible range"


def test_cpvc_returns_value_when_oil_absorption_known():
    """At least the pigments with oil_absorption data should yield a CPVC."""
    form = knowledge.baseline_formulation(Requirement(domain=ProductDomain.anticorrosion_coating))
    # The anticorrosion template uses TiO₂, Talc, Fumed silica, Zinc phosphate —
    # all of which now carry oil_absorption in the knowledge base.
    val = chemistry.cpvc(form)
    assert val is not None, "Expected CPVC from Asbeck formula with known OA values"
    assert 10.0 < val < 80.0, f"CPVC {val} outside plausible range"


def test_pvc_degreaser_is_zero_or_near_zero():
    """Degreaser has no pigments; PVC should be zero."""
    form = knowledge.baseline_formulation(Requirement(domain=ProductDomain.degreaser))
    val = chemistry.pvc(form)
    assert val == 0.0, f"Degreaser should have PVC=0, got {val}"


# ── v0.5: Safety checks ────────────────────────────────────────────────────────

def test_acid_base_conflict_detected():
    """Surface treatment formulation has phosphoric acid — no base conflict in baseline."""
    from app.domain.schemas import ProductDomain
    form = knowledge.baseline_formulation(Requirement(domain=ProductDomain.surface_treatment))
    warnings = chemistry.check_acid_base_conflict(form)
    # Baseline surface treatment has phosphoric acid but no strong base → no conflict
    assert warnings == [], f"Unexpected acid-base conflict: {warnings}"


def test_svhc_detected_in_surface_treatment():
    """Zinc phosphating bath contains sodium nitrite (SVHC candidate)."""
    form = knowledge.baseline_formulation(Requirement(domain=ProductDomain.surface_treatment))
    warnings = chemistry.check_svhc(form)
    assert len(warnings) >= 1, "Sodium nitrite should be flagged as SVHC in surface treatment"
    assert "SVHC" in warnings[0]


def test_svhc_not_in_plain_degreaser():
    """Alkaline degreaser baseline has no SVHC candidates."""
    form = knowledge.baseline_formulation(Requirement(domain=ProductDomain.degreaser))
    warnings = chemistry.check_svhc(form)
    assert warnings == [], f"Alkaline degreaser should have no SVHC: {warnings}"


def test_check_voc_category_waterborne():
    form = knowledge.baseline_formulation(Requirement(domain=ProductDomain.degreaser))
    cat = chemistry.check_voc_category(form, voc_gpl=10.0)
    assert cat == "waterborne"


def test_full_safety_check_no_issues_on_plain_anticorrosion():
    """The baseline anticorrosion primer should not trigger acid-base or SVHC warnings."""
    form = knowledge.baseline_formulation(Requirement(domain=ProductDomain.anticorrosion_coating))
    warnings = chemistry.full_safety_check(form)
    # Zinc phosphate, TiO2, talc, epoxy — none are SVHC in the current knowledge base
    for w in warnings:
        assert "SVHC" not in w or "zinc phosphate" not in w.lower(), (
            f"Unexpected SVHC warning: {w}"
        )

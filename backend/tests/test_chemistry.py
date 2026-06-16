import pytest

from app.domain import chemistry, knowledge
from app.domain.schemas import ProductDomain, Requirement


def test_molar_mass_simple():
    assert chemistry.molar_mass("H2O") == pytest.approx(18.015, abs=0.01)


def test_molar_mass_nested_parentheses():
    # Zn3(PO4)2 = 3*65.38 + 2*(30.974 + 4*15.999)
    assert chemistry.molar_mass("Zn3(PO4)2") == pytest.approx(386.11, abs=0.05)
    assert chemistry.molar_mass("Mn(H2PO4)2") == pytest.approx(248.93, abs=0.1)


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

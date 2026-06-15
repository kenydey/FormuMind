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

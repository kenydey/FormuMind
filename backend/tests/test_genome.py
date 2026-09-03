"""Formulation genome: variable-topology composition.

The factor path can only rescale ingredients already present in a hardcoded
template. These tests pin the properties that make the genome path usable for
inverse design and substitution — above all that a swap actually changes the
composition, and that unit handling survives one.
"""
from __future__ import annotations

import pytest

from app.domain import knowledge
from app.domain.chemistry import is_waterborne
from app.domain.genome import (
    FormulationGenome,
    Slot,
    UnknownMaterialError,
    candidates_for_role,
    formulation_from_genome,
    genome_from_formulation,
)
from app.domain.schemas import ProductDomain, Requirement, Substrate
from app.pipeline import reconstruct


def _anticorrosion() -> Requirement:
    return Requirement(domain=ProductDomain.anticorrosion_coating)


def _surface_treatment_mg() -> Requirement:
    return Requirement(
        domain=ProductDomain.surface_treatment, substrate=Substrate.magnesium_alloy
    )


# ── round trip ───────────────────────────────────────────────────────────────


def test_round_trip_preserves_composition():
    req = _anticorrosion()
    base = knowledge.baseline_formulation(req)
    genome = genome_from_formulation(base)
    rebuilt = formulation_from_genome(req, genome)

    assert [i.name for i in rebuilt.ingredients] == [i.name for i in base.ingredients]
    for before, after in zip(base.ingredients, rebuilt.ingredients):
        assert after.weight_pct == pytest.approx(before.weight_pct, abs=0.01)
    assert rebuilt.total_pct() == pytest.approx(100.0, abs=0.01)


@pytest.mark.parametrize(
    "req",
    [
        Requirement(domain=ProductDomain.anticorrosion_coating),
        Requirement(domain=ProductDomain.degreaser),
        Requirement(
            domain=ProductDomain.surface_treatment, substrate=Substrate.magnesium_alloy
        ),
        Requirement(
            domain=ProductDomain.surface_treatment, substrate=Substrate.carbon_steel
        ),
    ],
    ids=["anticorrosion", "degreaser", "st_magnesium", "st_steel"],
)
def test_round_trip_via_reconstruct_helper_is_lossless(req):
    """Per-ingredient equality, not just weight closure — _balanced() forces the
    total to 100 either way, so a total-only assertion hides unit errors."""
    base = knowledge.baseline_formulation(req)
    genome = reconstruct.genome_from_requirement(req)
    rebuilt = reconstruct.formulation_from_genome(req, genome)

    before = {i.name: i.weight_pct for i in base.ingredients}
    after = {i.name: i.weight_pct for i in rebuilt.ingredients}
    assert before.keys() == after.keys()
    for name, value in before.items():
        assert after[name] == pytest.approx(value, abs=0.01), name


def test_g_per_litre_slot_does_not_shift_by_ten():
    """Surface-treatment levers are g/L; the ×0.1 conversion is name-keyed in
    the factor path and would be lost the moment a slot's material changes."""
    req = _surface_treatment_mg()
    base = knowledge.baseline_formulation(req)
    genome = reconstruct.genome_from_requirement(req)

    zr_slot = next(s for s in genome.slots if s.material == "Hexafluorozirconic acid")
    assert zr_slot.unit == "g/L"
    # 1.2 wt% in the template is carried as 12 g/L in the slot.
    assert zr_slot.weight_pct == pytest.approx(12.0, abs=0.01)
    assert zr_slot.to_wt_pct() == pytest.approx(1.2, abs=0.01)

    rebuilt = formulation_from_genome(req, genome)
    zr_before = next(i for i in base.ingredients if i.name == "Hexafluorozirconic acid")
    zr_after = next(i for i in rebuilt.ingredients if i.name == "Hexafluorozirconic acid")
    assert zr_after.weight_pct == pytest.approx(zr_before.weight_pct, abs=0.01)


# ── the capability the factor path lacks ─────────────────────────────────────


def test_swapping_a_material_changes_the_composition():
    """This is the whole point: the factor path cannot do this at all."""
    req = _anticorrosion()
    genome = reconstruct.genome_from_requirement(req)
    idx = next(i for i, s in enumerate(genome.slots) if s.role == "hardener")
    original = genome.slots[idx].material

    swapped = genome.with_material(idx, "Isophorone diamine (IPDA)")
    form = formulation_from_genome(req, swapped)
    names = [i.name for i in form.ingredients]

    assert "Isophorone diamine (IPDA)" in names
    assert original not in names
    assert form.total_pct() == pytest.approx(100.0, abs=0.01)


def test_swap_updates_role_from_catalog():
    req = _anticorrosion()
    genome = reconstruct.genome_from_requirement(req)
    idx = next(i for i, s in enumerate(genome.slots) if s.role == "hardener")
    swapped = genome.with_material(idx, "Isophorone diamine (IPDA)")
    assert swapped.slots[idx].role == "hardener"


def test_dropping_a_slot_removes_the_ingredient():
    req = _anticorrosion()
    genome = reconstruct.genome_from_requirement(req)
    kept = [s for s in genome.slots if s.role != "filler"]
    trimmed = FormulationGenome(domain=genome.domain, slots=kept, name=genome.name)
    form = formulation_from_genome(req, trimmed)
    assert all(i.role != "filler" for i in form.ingredients)
    assert form.total_pct() == pytest.approx(100.0, abs=0.01)


# ── failure modes are loud ───────────────────────────────────────────────────


def test_unknown_material_raises_instead_of_being_dropped():
    """formulation_from_factors silently discards unknown names, which makes a
    half-wired search look like it works. The genome path must not."""
    genome = FormulationGenome(
        domain=ProductDomain.anticorrosion_coating,
        slots=[Slot(role="resin", material="Nonexistent Resin", weight_pct=50.0)],
    )
    with pytest.raises(UnknownMaterialError):
        formulation_from_genome(_anticorrosion(), genome)


def test_unknown_material_tolerated_when_not_strict():
    genome = FormulationGenome(
        domain=ProductDomain.anticorrosion_coating,
        slots=[Slot(role="resin", material="Nonexistent Resin", weight_pct=50.0)],
    )
    form = formulation_from_genome(_anticorrosion(), genome, strict=False)
    assert form.ingredients[0].name == "Nonexistent Resin"


def test_empty_genome_raises():
    genome = FormulationGenome(domain=ProductDomain.anticorrosion_coating, slots=[])
    with pytest.raises(ValueError):
        formulation_from_genome(_anticorrosion(), genome)


def test_zero_weight_slots_are_dropped():
    req = _anticorrosion()
    genome = FormulationGenome(
        domain=ProductDomain.anticorrosion_coating,
        slots=[
            Slot(role="resin", material="Bisphenol-A epoxy (DGEBA)", weight_pct=60.0),
            Slot(role="hardener", material="Polyamide hardener", weight_pct=0.0),
            Slot(role="solvent", material="Xylene", weight_pct=40.0),
        ],
    )
    form = formulation_from_genome(req, genome)
    assert "Polyamide hardener" not in [i.name for i in form.ingredients]


# ── candidate pools ──────────────────────────────────────────────────────────


def test_candidates_for_role_returns_same_role_materials():
    hardeners = candidates_for_role("hardener")
    assert "Polyamide hardener" in hardeners
    assert all(knowledge.RAW_MATERIALS[n]["role"] == "hardener" for n in hardeners)


def test_candidates_exclude_solvent_carrier_in_waterborne_system():
    """A solvent-borne blocked isocyanate is not a candidate in a water system."""
    waterborne = Requirement(
        domain=ProductDomain.anticorrosion_coating, voc_limit_gpl=100
    )
    pool = candidates_for_role("hardener", waterborne)
    assert "Desmodur BL 3175" not in pool
    assert "Waterborne polyisocyanate (hydrophilic HDI)" in pool


def test_candidates_include_solvent_carrier_in_solventborne_system():
    solventborne = Requirement(
        domain=ProductDomain.anticorrosion_coating, voc_limit_gpl=420
    )
    assert "Desmodur BL 3175" in candidates_for_role("hardener", solventborne)


def test_swappable_excludes_locked_and_fixed_roles():
    genome = FormulationGenome(
        domain=ProductDomain.anticorrosion_coating,
        slots=[
            Slot(role="resin", material="Bisphenol-A epoxy (DGEBA)", weight_pct=40.0),
            Slot(role="hardener", material="Polyamide hardener", weight_pct=15.0, locked=True),
            Slot(role="solvent", material="Xylene", weight_pct=45.0),
        ],
    )
    roles = {s.role for s in genome.swappable()}
    assert roles == {"resin"}


# ── waterborne detection is name-independent ─────────────────────────────────


def test_waterborne_detection_uses_carrier_not_name():
    """A swapped-in aqueous carrier must still register as waterborne."""
    req = Requirement(domain=ProductDomain.degreaser)
    base = knowledge.baseline_formulation(req)
    assert is_waterborne(base)


def test_solventborne_formulation_is_not_waterborne():
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating, voc_limit_gpl=420
    )
    assert not is_waterborne(knowledge.baseline_formulation(req))

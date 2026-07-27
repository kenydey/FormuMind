"""In-loop feasibility gate for genome search.

The expert review already knew a solvent-borne blocked isocyanate cannot go
into a waterborne system — but nothing consumed that verdict as a gate. These
tests pin that it now rejects candidates, and that it does so without touching
the network, which is what makes it usable inside a search loop.
"""
from __future__ import annotations

import pytest

from app.domain.genome import FormulationGenome, Slot
from app.domain.schemas import ProductDomain, Requirement
from app.services import feasibility
from app.services.feasibility import (
    filter_feasible,
    genome_is_feasible,
)


def _waterborne_req() -> Requirement:
    return Requirement(domain=ProductDomain.anticorrosion_coating, voc_limit_gpl=100)


def _waterborne_genome(hardener: str) -> FormulationGenome:
    """>30 wt% water so the chemist agent's waterborne rule engages."""
    return FormulationGenome(
        domain=ProductDomain.anticorrosion_coating,
        name="test waterborne",
        slots=[
            Slot(role="resin", material="Waterborne acrylic emulsion", weight_pct=25.0),
            Slot(role="hardener", material=hardener, weight_pct=10.0),
            Slot(role="solvent", material="Deionized water", weight_pct=65.0),
        ],
    )


# ── the interception that matters ────────────────────────────────────────────


def test_solvent_borne_hardener_in_water_is_rejected():
    verdict = genome_is_feasible(_waterborne_genome("Desmodur BL 3175"), _waterborne_req())
    assert not verdict
    assert verdict.status == "intercept"
    assert any("isocyanate" in r or "不相容" in r for r in verdict.reasons)


def test_waterborne_hardener_in_water_is_accepted():
    verdict = genome_is_feasible(
        _waterborne_genome("Waterborne polyisocyanate (hydrophilic HDI)"),
        _waterborne_req(),
    )
    assert verdict
    assert verdict.status in ("pass", "warn")


def test_intercepts_on_the_real_waterborne_template():
    """The hand-built genomes above put 65 wt% water in one slot. The actual
    waterborne primer never does — it is 38% acrylic emulsion plus 20% water —
    so a rule requiring one component to clear 30% alone classified it
    solvent-borne and the gate stayed silent on the system it exists to guard.
    """
    from app.pipeline import reconstruct

    req = _waterborne_req()
    genome = reconstruct.genome_from_requirement(req)
    idx = next(i for i, s in enumerate(genome.slots) if s.role == "hardener")

    verdict = genome_is_feasible(genome.with_material(idx, "Desmodur BL 3175"), req)
    assert not verdict
    assert verdict.status == "intercept"


def test_real_solventborne_template_is_not_intercepted():
    """Guard the other direction: the same swap must stay legal in solvent."""
    from app.pipeline import reconstruct

    req = Requirement(domain=ProductDomain.anticorrosion_coating, voc_limit_gpl=420)
    genome = reconstruct.genome_from_requirement(req)
    idx = next(i for i, s in enumerate(genome.slots) if s.role == "hardener")
    assert genome_is_feasible(genome.with_material(idx, "Desmodur BL 3175"), req)


def test_same_hardener_is_fine_in_a_solvent_borne_system():
    """The rule is about the carrier, not the material in isolation."""
    genome = FormulationGenome(
        domain=ProductDomain.anticorrosion_coating,
        slots=[
            Slot(role="resin", material="Bisphenol-A epoxy (DGEBA)", weight_pct=40.0),
            Slot(role="hardener", material="Desmodur BL 3175", weight_pct=15.0),
            Slot(role="solvent", material="Xylene", weight_pct=45.0),
        ],
    )
    req = Requirement(domain=ProductDomain.anticorrosion_coating, voc_limit_gpl=420)
    assert genome_is_feasible(genome, req)


# ── it must be cheap enough for a search loop ────────────────────────────────


def test_gate_never_calls_the_llm(monkeypatch):
    """explain defaults to True, and the chemist agent then makes a blocking
    LLM call for every candidate carrying an issue. In a loop that is one
    network round-trip per evaluation."""
    from app.services import llm as llm_service

    calls: list[str] = []

    def _boom(*args, **kwargs):
        calls.append("llm")
        raise AssertionError("feasibility gate must not call the LLM")

    monkeypatch.setattr(llm_service, "complete_json", _boom)
    monkeypatch.setattr(llm_service, "_call_llm", _boom)

    # A genome that *does* produce issues — the path that would trigger it.
    genome_is_feasible(_waterborne_genome("Desmodur BL 3175"), _waterborne_req())
    assert calls == []


# ── failure modes ────────────────────────────────────────────────────────────


def test_unbuildable_genome_is_infeasible_not_an_exception():
    genome = FormulationGenome(
        domain=ProductDomain.anticorrosion_coating,
        slots=[Slot(role="resin", material="Nonexistent Resin", weight_pct=50.0)],
    )
    verdict = genome_is_feasible(genome, _waterborne_req())
    assert not verdict
    assert verdict.status == "invalid"
    assert verdict.reasons


def test_empty_genome_is_infeasible():
    genome = FormulationGenome(domain=ProductDomain.anticorrosion_coating, slots=[])
    assert not genome_is_feasible(genome, _waterborne_req())


def test_gate_degrades_open_when_the_reviewer_breaks(monkeypatch):
    """A broken reviewer must not silently empty the search space."""
    import app.agents.supervisor as supervisor_mod

    class _Broken:
        def review(self, *args, **kwargs):
            raise RuntimeError("reviewer down")

    monkeypatch.setattr(supervisor_mod, "InitializeAgent", _Broken)
    verdict = genome_is_feasible(
        _waterborne_genome("Desmodur BL 3175"), _waterborne_req()
    )
    assert verdict.feasible


# ── batch filtering ──────────────────────────────────────────────────────────


def test_filter_feasible_splits_and_reports_rejects():
    good = _waterborne_genome("Waterborne polyisocyanate (hydrophilic HDI)")
    bad = _waterborne_genome("Desmodur BL 3175")
    keep, rejected = filter_feasible([good, bad, good], _waterborne_req())
    assert len(keep) == 2
    assert len(rejected) == 1
    # The caller can explain why the space shrank rather than silently dropping.
    assert rejected[0].reasons


def test_filter_feasible_on_empty_input():
    keep, rejected = filter_feasible([], _waterborne_req())
    assert keep == [] and rejected == []


def test_verdict_is_truthy_by_feasibility():
    assert bool(feasibility.FeasibilityVerdict(feasible=True))
    assert not bool(feasibility.FeasibilityVerdict(feasible=False))

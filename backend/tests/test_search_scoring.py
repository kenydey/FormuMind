"""Tests for live Evidence chemical-entity scoring (Phase B R-2b)."""
from __future__ import annotations

from app.domain.schemas import Evidence
from app.services.search_scoring import evidence_entity_boost, query_chem_context


def test_query_chem_context_extracts_cas():
    ctx = query_chem_context("epoxy primer with CAS 7779-90-0 zinc phosphate")
    assert "7779-90-0" in ctx["cas"]


def test_evidence_entity_boost_for_matching_cas():
    ctx = query_chem_context("7779-90-0 zinc phosphate")
    ev = Evidence(
        source="patent",
        identifier="US9982145B2",
        title="Zinc phosphate primer",
        snippet="Anti-corrosion primer using zinc phosphate CAS 7779-90-0",
        relevance=0.5,
    )
    assert evidence_entity_boost(ev, ctx) >= 0.3


def test_evidence_entity_boost_zero_without_entities():
    ctx = query_chem_context("generic coating performance")
    ev = Evidence(
        source="patent",
        identifier="X1",
        title="Coating",
        snippet="Performance data",
        relevance=0.5,
    )
    assert evidence_entity_boost(ev, ctx) == 0.0

"""Tests for KG-driven chemical feasibility gate (kg_chemical_check)."""

from __future__ import annotations

import pytest

from app.domain.schemas import Formulation, Ingredient, ProductDomain
from app.services.kg_chemical_check import (
    ChemicalCheckResult,
    check_formulation_chemistry,
)


def _form(*names: str) -> Formulation:
    return Formulation(
        name="test",
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[Ingredient(name=n, role="additive", weight_pct=10.0) for n in names],
    )


def test_kg_disabled_is_pass(monkeypatch):
    """When KG is disabled the check must degrade to feasible (no constraint)."""

    class _S:
        kg_enabled = False

    monkeypatch.setattr("app.services.kg_chemical_check.get_settings", lambda: _S())
    res = check_formulation_chemistry(_form("环氧树脂", "固化剂A"))
    assert res.feasible is True
    assert res.status == "pass"


def test_inhibits_relation_marks_infeasible(monkeypatch):
    """A concrete INHIBITS relation between two resolved materials → infeasible."""

    class _S:
        kg_enabled = True

    monkeypatch.setattr("app.services.kg_chemical_check.get_settings", lambda: _S())

    # Resolve both material names to fake entity ids.
    monkeypatch.setattr(
        "app.services.kg_chemical_check._resolve_entity_id",
        lambda name: f"ent:{name}",
    )

    # Material A's relations include an INHIBITS edge to material B.
    class _Rel:
        confidence = 0.9

        def __init__(self, other):
            self.relation_type = type("T", (), {"value": "inhibits"})()
            self.target_entity_id = other
            self.source_entity_id = "ent:X"
            self.evidence = [type("E", (), {"sentence": "文献报道两者不相容"})()]

    monkeypatch.setattr(
        "app.services.kg_chemical_check._incompatible_pairs_for",
        lambda eid: [("ent:固化剂A", _Rel("ent:固化剂A"), "文献报道两者不相容")]
        if eid == "ent:环氧树脂"
        else [],
    )

    res = check_formulation_chemistry(_form("环氧树脂", "固化剂A"))
    assert res.feasible is False
    assert res.status == "infeasible"
    assert len(res.incompatible_pairs) >= 1
    assert any("不相容" in r for r in res.reasons)


def test_no_relation_is_pass(monkeypatch):
    """Two resolved materials with no INHIBITS relation → feasible."""

    class _S:
        kg_enabled = True

    monkeypatch.setattr("app.services.kg_chemical_check.get_settings", lambda: _S())
    monkeypatch.setattr(
        "app.services.kg_chemical_check._resolve_entity_id",
        lambda name: f"ent:{name}",
    )
    monkeypatch.setattr(
        "app.services.kg_chemical_check._incompatible_pairs_for",
        lambda eid: [],
    )
    res = check_formulation_chemistry(_form("环氧树脂", "固化剂A"))
    assert res.feasible is True


def test_single_material_is_pass(monkeypatch):
    """Fewer than two resolved materials → no pair-wise check possible."""

    class _S:
        kg_enabled = True

    monkeypatch.setattr("app.services.kg_chemical_check.get_settings", lambda: _S())
    res = check_formulation_chemistry(_form("环氧树脂"))
    assert res.feasible is True

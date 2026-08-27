"""Tests for KG → recommendation ranking adapter (kg_recommend_score)."""

from __future__ import annotations

import pytest

from app.domain.schemas import Formulation, Ingredient, ProductDomain
from app.services.kg_chemical_check import ChemicalCheckResult
from app.services.kg_recommend_score import kg_compat_adjust, record_kg_compat


def _form(*names: str, score: float | None = 0.8) -> Formulation:
    f = Formulation(
        name="test",
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[Ingredient(name=n, role="additive", weight_pct=10.0) for n in names],
    )
    f.score = score
    return f


def test_kg_disabled_noop(monkeypatch):
    """KG off → score untouched, no kg_compat recorded."""

    class _S:
        kg_enabled = False

    monkeypatch.setattr("app.services.kg_recommend_score.get_settings", lambda: _S())
    f = _form("环氧树脂", "固化剂A", score=0.9)
    chk = kg_compat_adjust(f)
    assert f.score == 0.9
    assert chk.feasible is True
    assert f.kg_compat is None


def test_inhibits_penalizes_score(monkeypatch):
    """INHIBITS relation → score multiplied by penalty + warning + kg_compat."""

    class _S:
        kg_enabled = True
        kg_inhibits_penalty = 0.5
        kg_synergizes_bonus = 1.0

    # Patch BOTH settings entry points (kg_recommend_score and kg_chemical_check).
    monkeypatch.setattr("app.services.kg_recommend_score.get_settings", lambda: _S())
    monkeypatch.setattr("app.services.kg_chemical_check.get_settings", lambda: _S())
    monkeypatch.setattr(
        "app.services.kg_chemical_check._resolve_entity_id",
        lambda name: f"ent:{name}",
    )

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
    monkeypatch.setattr(
        "app.services.kg_chemical_check._synergy_pairs_for", lambda eid: []
    )

    f = _form("环氧树脂", "固化剂A", score=0.8)
    chk = kg_compat_adjust(f)
    assert chk.feasible is False
    assert f.score == pytest.approx(0.4)  # 0.8 * 0.5
    assert any("不相容" in w for w in f.warnings)
    assert f.kg_compat is not None
    assert f.kg_compat["feasible"] is False
    assert len(f.kg_compat["incompatible_pairs"]) >= 1


def test_synergizes_bonus_default_off(monkeypatch):
    """SYNERGIZES with default bonus=1.0 → no score change."""

    class _S:
        kg_enabled = True
        kg_inhibits_penalty = 0.5
        kg_synergizes_bonus = 1.0

    monkeypatch.setattr("app.services.kg_recommend_score.get_settings", lambda: _S())
    monkeypatch.setattr("app.services.kg_chemical_check.get_settings", lambda: _S())
    monkeypatch.setattr(
        "app.services.kg_chemical_check._resolve_entity_id",
        lambda name: f"ent:{name}",
    )
    monkeypatch.setattr(
        "app.services.kg_chemical_check._incompatible_pairs_for", lambda eid: []
    )

    class _Rel:
        confidence = 0.9

        def __init__(self, other):
            self.relation_type = type("T", (), {"value": "synergizes"})()
            self.target_entity_id = other
            self.source_entity_id = "ent:X"
            self.evidence = []

    monkeypatch.setattr(
        "app.services.kg_chemical_check._synergy_pairs_for",
        lambda eid: [("ent:固化剂A", _Rel("ent:固化剂A"), "")]
        if eid == "ent:环氧树脂"
        else [],
    )

    f = _form("环氧树脂", "固化剂A", score=0.8)
    chk = kg_compat_adjust(f)
    assert chk.feasible is True
    assert f.score == pytest.approx(0.8)  # bonus 1.0 → unchanged


def test_synergizes_bonus_applied(monkeypatch):
    """SYNERGIZES with bonus=1.1 → score bumped."""

    class _S:
        kg_enabled = True
        kg_inhibits_penalty = 0.5
        kg_synergizes_bonus = 1.1

    monkeypatch.setattr("app.services.kg_recommend_score.get_settings", lambda: _S())
    monkeypatch.setattr("app.services.kg_chemical_check.get_settings", lambda: _S())
    monkeypatch.setattr(
        "app.services.kg_chemical_check._resolve_entity_id",
        lambda name: f"ent:{name}",
    )
    monkeypatch.setattr(
        "app.services.kg_chemical_check._incompatible_pairs_for", lambda eid: []
    )

    class _Rel:
        confidence = 0.9

        def __init__(self, other):
            self.relation_type = type("T", (), {"value": "synergizes"})()
            self.target_entity_id = other
            self.source_entity_id = "ent:X"
            self.evidence = []

    monkeypatch.setattr(
        "app.services.kg_chemical_check._synergy_pairs_for",
        lambda eid: [("ent:固化剂A", _Rel("ent:固化剂A"), "")]
        if eid == "ent:环氧树脂"
        else [],
    )

    f = _form("环氧树脂", "固化剂A", score=0.8)
    kg_compat_adjust(f)
    assert f.score == pytest.approx(0.88)  # 0.8 * 1.1


def test_record_kg_compat_shape():
    chk = ChemicalCheckResult(
        feasible=False,
        status="infeasible",
        reasons=["A 与 B 不相容"],
        incompatible_pairs=[("A", "B", "inhibits")],
        synergy_pairs=[("C", "D", "synergizes")],
    )
    f = _form("A", "B")
    record_kg_compat(f, chk)
    assert f.kg_compat["feasible"] is False
    assert f.kg_compat["incompatible_pairs"][0]["relation"] == "inhibits"
    assert f.kg_compat["synergy_pairs"][0]["relation"] == "synergizes"

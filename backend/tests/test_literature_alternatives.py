"""L2 literature alternatives — KG + KB products, degrade-friendly."""
from __future__ import annotations

from types import SimpleNamespace

from app.services.literature_alternatives import fetch_literature_alternatives


def test_fetch_literature_empty_material():
    out = fetch_literature_alternatives(material="")
    assert out["literature"] == []
    assert out["literature_meta"]["skipped_reason"] == "empty_material"


def test_fetch_literature_merges_kg_and_kb(monkeypatch):
    fake_cand = SimpleNamespace(
        entity_id="chem:x",
        entity_name="Lit Amine X",
        confidence=0.81,
        path=[],
    )
    monkeypatch.setattr(
        "app.services.kg.retrieval.kg_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.kg.retrieval.resolve_query",
        lambda q: SimpleNamespace(chemicals=[SimpleNamespace(id="chem:seed")], trade_products=[]),
    )
    monkeypatch.setattr(
        "app.services.kg.graph_query.discover_substitutes",
        lambda entity_id, limit=8: SimpleNamespace(substitutes=[fake_cand]),
    )

    class _Prod:
        id = "p1"
        trade_name = "Trade Y"
        generic_name = "KB Product Y"
        cas = "123-45-6"
        smiles = None
        role = "hardener"
        mention_count = 3

    class _Store:
        def find_for_material(self, material, cas=""):
            return [_Prod()]

        def search(self, q, limit=8):
            return []

    monkeypatch.setattr(
        "app.db.product_store.get_product_store",
        lambda: _Store(),
    )

    out = fetch_literature_alternatives(
        material="Polyamide hardener", role_hint="hardener", limit=8
    )
    names = [r["name"] for r in out["literature"]]
    assert "Lit Amine X" in names
    assert "KB Product Y" in names
    assert "kg" in out["literature_meta"]["providers"]
    assert "kb_product" in out["literature_meta"]["providers"]


def test_fetch_literature_kg_disabled_still_tries_kb(monkeypatch):
    monkeypatch.setattr("app.services.kg.retrieval.kg_enabled", lambda: False)

    class _Prod:
        id = "p2"
        trade_name = ""
        generic_name = "Only KB Hit"
        cas = ""
        smiles = None
        role = None
        mention_count = 1

    class _Store:
        def find_for_material(self, material, cas=""):
            return [_Prod()]

        def search(self, q, limit=8):
            return []

    monkeypatch.setattr("app.db.product_store.get_product_store", lambda: _Store())
    out = fetch_literature_alternatives(material="Zinc phosphate", limit=5)
    assert out["literature"][0]["name"] == "Only KB Hit"
    assert out["literature_meta"]["providers"] == ["kb_product"]

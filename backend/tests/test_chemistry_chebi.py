"""ChEBI connector lookup tests."""
from __future__ import annotations

from app.services import chemistry_chebi as chebi
from app.services import connectors_builtin as conn


def test_lookup_chebi_empty():
    assert chebi.lookup_chebi("") == []


def test_lookup_chebi_mock(monkeypatch):
    class Resp:
        status_code = 200

        def json(self):
            return {
                "response": {
                    "docs": [
                        {
                            "obo_id": "CHEBI:15377",
                            "label": "water",
                            "description": ["oxidane"],
                            "iri": "http://purl.obolibrary.org/obo/CHEBI_15377",
                        }
                    ]
                }
            }

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return Resp()

    class FakeHttpx:
        Client = FakeClient

    monkeypatch.setitem(__import__("sys").modules, "httpx", FakeHttpx)
    rows = chebi.lookup_chebi("water", limit=2)
    assert rows and rows[0]["chebi_id"] == "CHEBI:15377"
    assert rows[0]["name"] == "water"


def test_lookup_chemistry_includes_chebi(monkeypatch):
    monkeypatch.setattr(
        conn,
        "lookup_compound",
        lambda q: {},
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.compounds.lookup_compound",
        lambda q: {"query": q},
    )
    monkeypatch.setattr(
        "app.services.chemistry_chebi.lookup_chebi",
        lambda q, limit=5: [
            {"chebi_id": "CHEBI:1", "name": q, "description": "demo", "iri": None}
        ],
    )
    # Avoid pubchempy network
    import sys

    monkeypatch.setitem(sys.modules, "pubchempy", None)
    rows = conn.lookup_chemistry("epoxy", limit=3)
    assert any(r.source == "chebi" for r in rows)


def test_catalog_lists_chebi():
    chem = next(c for c in conn.CONNECTOR_CATALOG if c["id"] == "chemistry")
    assert "ChEBI" in chem["sources"]

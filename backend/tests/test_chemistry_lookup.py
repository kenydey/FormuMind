"""Tests for chemical lookup API."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_chemical_lookup_local_material():
    res = client.get("/api/chemical/lookup", params={"q": "Bisphenol-A epoxy (DGEBA)"})
    assert res.status_code == 200
    body = res.json()
    assert "cas" in body
    assert body.get("iupac_name")


def test_chemical_lookup_empty_query_rejected():
    res = client.get("/api/chemical/lookup", params={"q": ""})
    assert res.status_code == 422


def test_chemical_lookup_compound_synonyms(monkeypatch):
    from app.services import compounds

    def fake_lookup(q: str):
        return {
            "query": q,
            "cas": "7779-90-0",
            "iupac_name": "Zinc phosphate",
            "zh_name": "磷酸锌",
            "formula": "Zn3(PO4)2",
            "smiles": "O=P(O)(O)O.[Zn].[Zn].[Zn]",
            "molar_mass": 386.0,
        }

    monkeypatch.setattr(compounds, "lookup_compound", fake_lookup)
    from app.services import chemical_lookup

    chemical_lookup._CACHE.clear()
    res = client.get("/api/chemical/lookup", params={"q": "ObscureTradeName-42"})
    assert res.status_code == 200
    body = res.json()
    assert body["cas"] == "7779-90-0"
    assert body["zh_name"] == "磷酸锌"
    assert body["source"] == "pubchempy_compound"

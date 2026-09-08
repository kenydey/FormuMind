"""SureChEMBL official API client + lookup fallback (mocked, offline CI)."""
from __future__ import annotations

import pytest

from app.services import chemical_lookup, surechembl_client, surechembl_lookup


@pytest.fixture(autouse=True)
def _clear_caches():
    surechembl_client.clear_surechembl_cache()
    chemical_lookup._CACHE.clear()
    yield
    surechembl_client.clear_surechembl_cache()
    chemical_lookup._CACHE.clear()


def test_get_by_name_parses_envelope(monkeypatch):
    monkeypatch.setattr(surechembl_client, "surechembl_enabled", lambda: True)

    def _fake_get(path, timeout=2.0):
        assert "/chemical/name/" in path
        return {
            "status": "OK",
            "data": [
                {
                    "id": "29309",
                    "chemical_id": "29309",
                    "name": "phosphoric acid zinc",
                    "smiles": "[Zn].OP(O)(O)=O",
                    "inchi_key": "OXHXATNDTXVKAU-UHFFFAOYSA-N",
                    "mol_weight": 163.4,
                    "global_frequency": 323,
                }
            ],
        }

    monkeypatch.setattr(surechembl_client, "_http_get_json", _fake_get)
    rows = surechembl_client.get_by_name("zinc phosphate")
    assert len(rows) == 1
    assert rows[0]["chemical_id"] == "29309"
    assert rows[0]["smiles"].startswith("[Zn]")


def test_get_by_smiles_parses_nested_map(monkeypatch):
    monkeypatch.setattr(surechembl_client, "surechembl_enabled", lambda: True)

    def _fake_get(path, timeout=2.0):
        assert path.endswith("/chemical/smiles/CCO/")
        return {
            "status": "OK",
            "data": {
                "CCO": {
                    "id": "463",
                    "chemical_id": "463",
                    "name": "ethanol",
                    "smiles": "CCO",
                    "mol_weight": 46.07,
                    "global_frequency": 592,
                }
            },
        }

    monkeypatch.setattr(surechembl_client, "_http_get_json", _fake_get)
    hit = surechembl_client.get_by_smiles("CCO")
    assert hit is not None
    assert hit["name"] == "ethanol"
    assert hit["chemical_id"] == "463"


def test_client_disabled_short_circuits(monkeypatch):
    monkeypatch.setattr(surechembl_client, "surechembl_enabled", lambda: False)
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("should not call network")

    monkeypatch.setattr(surechembl_client, "_http_get_json", _boom)
    assert surechembl_client.get_by_name("x") == []
    assert surechembl_client.get_by_smiles("CCO") is None
    assert called["n"] == 0


def test_client_http_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(surechembl_client, "surechembl_enabled", lambda: True)
    monkeypatch.setattr(surechembl_client, "_http_get_json", lambda *a, **k: None)
    assert surechembl_client.get_by_name("nope") == []
    assert surechembl_client.get_by_smiles("CCO") is None


def test_lookup_surechembl_shapes_chemical_lookup_payload(monkeypatch):
    monkeypatch.setattr(surechembl_client, "surechembl_enabled", lambda: True)
    monkeypatch.setattr(
        surechembl_client,
        "get_by_name",
        lambda q, timeout=2.0: [
            {
                "chemical_id": "1",
                "name": "Fake Chem",
                "smiles": "CCO",
                "inchi_key": "ABC",
                "mol_weight": 46.0,
                "global_frequency": 10,
            }
        ],
    )
    monkeypatch.setattr(surechembl_client, "get_by_smiles", lambda *a, **k: None)
    hit = surechembl_lookup.lookup_surechembl("Fake Chem")
    assert hit and hit["found"] is True
    assert hit["source"] == "surechembl"
    assert hit["cas"] == ""
    assert hit["surechembl"]["chemical_id"] == "1"
    assert hit["smiles"] == "CCO"


def test_lookup_chemical_falls_back_to_surechembl(monkeypatch):
    monkeypatch.setattr(chemical_lookup, "_lookup_catalog", lambda q: None)
    monkeypatch.setattr(chemical_lookup, "_lookup_pubchem", lambda q: None)
    monkeypatch.setattr(chemical_lookup, "_lookup_compound_synonyms", lambda q: None)
    monkeypatch.setattr(chemical_lookup, "_lookup_offline_compounds", lambda q: None)
    monkeypatch.setattr(chemical_lookup, "_lookup_chemtools", lambda q: None)
    monkeypatch.setattr(
        chemical_lookup,
        "_lookup_surechembl",
        lambda q: {
            "query": q,
            "cas": "",
            "iupac_name": "Sure Hit",
            "zh_name": "",
            "formula": "",
            "smiles": "C",
            "molar_mass": 12.0,
            "found": True,
            "source": "surechembl",
            "surechembl": {"chemical_id": "99", "global_frequency": 3},
        },
    )
    hit = chemical_lookup.lookup_chemical("ObscurePatentOnlyChem")
    assert hit["found"] is True
    assert hit["source"] == "surechembl"
    assert "surechembl" in hit["providers_tried"]
    assert hit["surechembl"]["chemical_id"] == "99"


def test_lookup_chemical_skips_surechembl_when_catalog_hits(monkeypatch):
    called = {"sure": 0}

    def _sure(q):
        called["sure"] += 1
        return None

    monkeypatch.setattr(chemical_lookup, "_lookup_surechembl", _sure)
    hit = chemical_lookup.lookup_chemical("Zinc phosphate")
    assert hit["found"] is True
    assert hit["source"] == "catalog"
    assert called["sure"] == 0


def test_chemical_lookup_endpoint_includes_surechembl_fields(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(chemical_lookup, "_lookup_catalog", lambda q: None)
    monkeypatch.setattr(chemical_lookup, "_lookup_pubchem", lambda q: None)
    monkeypatch.setattr(chemical_lookup, "_lookup_compound_synonyms", lambda q: None)
    monkeypatch.setattr(chemical_lookup, "_lookup_offline_compounds", lambda q: None)
    monkeypatch.setattr(chemical_lookup, "_lookup_chemtools", lambda q: None)
    monkeypatch.setattr(
        chemical_lookup,
        "_lookup_surechembl",
        lambda q: {
            "query": q,
            "cas": "",
            "iupac_name": "API Sure",
            "zh_name": "",
            "formula": "",
            "smiles": "CC",
            "molar_mass": 30.0,
            "found": True,
            "source": "surechembl",
            "surechembl": {
                "chemical_id": "42",
                "global_frequency": 7,
                "source_url": "https://www.surechembl.org/chemical/42",
            },
        },
    )
    client = TestClient(app)
    res = client.get("/api/chemical/lookup", params={"q": "API Sure"})
    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "surechembl"
    assert body["surechembl"]["chemical_id"] == "42"
    assert "providers_tried" in body

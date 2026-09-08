"""Unit tests for PubChem external alternative helpers (mocked HTTP)."""
from __future__ import annotations

from app.services import external_alternatives as ext


def test_disabled_env_skips_network(monkeypatch):
    monkeypatch.setattr(ext, "external_substitutes_enabled", lambda: False)
    out = ext.fetch_external_alternatives(material="Ethanol", spec={"smiles": "CCO"})
    assert out["external"] == []
    assert out["external_meta"]["enabled"] is False


def test_no_smiles_skips_with_reason(monkeypatch):
    monkeypatch.setattr(ext, "external_substitutes_enabled", lambda: True)
    monkeypatch.setattr(
        ext,
        "resolve_slot_identity",
        lambda *a, **k: {
            "query": "Mystery Polymer X",
            "cas_no": "",
            "smiles": None,
            "cid": None,
            "source": "none",
            "resolved": False,
        },
    )
    out = ext.fetch_external_alternatives(material="Mystery Polymer X", spec={})
    assert out["external"] == []
    assert "SMILES" in (out["external_meta"]["skipped_reason"] or "")


def test_pubchem_similar_parses_property_table(monkeypatch):
    monkeypatch.setattr(ext, "external_substitutes_enabled", lambda: True)
    ext.clear_external_cache()

    def fake_get(url: str, *, timeout: float = 12.0):
        if "fastsimilarity_2d" in url and "/property/" in url:
            return {
                "PropertyTable": {
                    "Properties": [
                        {
                            "CID": 702,
                            "Title": "Ethanol",
                            "IUPACName": "ethanol",
                            "MolecularFormula": "C2H6O",
                            "MolecularWeight": 46.07,
                            "CanonicalSMILES": "CCO",
                        },
                        {
                            "CID": 887,
                            "Title": "Methanol",
                            "IUPACName": "methanol",
                            "MolecularFormula": "CH4O",
                            "MolecularWeight": 32.04,
                            "CanonicalSMILES": "CO",
                        },
                    ]
                }
            }
        if "RegistryNumber" in url or "synonyms" in url:
            return {
                "InformationList": {
                    "Information": [
                        {"CID": 887, "Synonym": ["Methyl alcohol", "67-56-1", "MeOH"]},
                    ]
                }
            }
        return None

    monkeypatch.setattr(ext, "_http_get_json", fake_get)
    rows = ext.pubchem_similar_by_smiles("CCO", threshold=85, limit=5)
    # Query SMILES CCO dropped; methanol remains.
    assert len(rows) == 1
    assert rows[0]["name"] == "Methanol"
    assert rows[0]["cas_no"] == "67-56-1"
    assert rows[0]["source"] == "pubchem_similar"

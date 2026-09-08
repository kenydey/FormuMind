"""SureChEMBL structure alternatives + patent attach (mocked, offline CI)."""
from __future__ import annotations

import pytest

from app.services import surechembl_alternatives as sa
from app.services import surechembl_client


@pytest.fixture(autouse=True)
def _clear_caches():
    surechembl_client.clear_surechembl_cache()
    yield
    surechembl_client.clear_surechembl_cache()


def test_google_patent_url_from_scpn():
    assert sa._google_patent_url("CN-104789083-B") == "https://patents.google.com/patent/CN104789083B"
    assert sa._google_patent_url("") is None


def test_noise_score_penalizes_multi_fragment():
    noisy = {
        "name": "2-(acetyloxy)benzoic acid platinum",
        "smiles": "CC(=O)Oc1ccccc1C(=O)O.[Pt]",
        "global_frequency": 1,
    }
    clean = {
        "name": "aspirin analog",
        "smiles": "CC(=O)Oc1ccccc1C(=O)O",
        "global_frequency": 50,
    }
    assert sa._noise_score(noisy) > sa._noise_score(clean)
    assert sa._noise_score(noisy) >= 3.5


def test_fetch_skips_without_smiles(monkeypatch):
    monkeypatch.setattr(surechembl_client, "surechembl_enabled", lambda: True)
    out = sa.fetch_surechembl_alternatives(material="x", smiles=None)
    assert out["surechembl"] == []
    assert "SMILES" in (out["surechembl_meta"]["skipped_reason"] or "")


def test_fetch_filters_noise_and_attaches_patents(monkeypatch):
    monkeypatch.setattr(surechembl_client, "surechembl_enabled", lambda: True)

    def _fake_search(smiles, **kwargs):
        return {
            "hits": [
                {
                    "chemical_id": "1",
                    "name": "query twin platinum",
                    "smiles": f"{smiles}.[Pt]",
                    "similarity": "0.99",
                    "global_frequency": 1,
                    "mol_formula": "C9H8O4Pt",
                    "mol_weight": 375.0,
                },
                {
                    "chemical_id": "2",
                    "name": "Useful Analog",
                    "smiles": "CC(=O)Oc1ccc(O)cc1C(=O)O",
                    "similarity": "0.92",
                    "global_frequency": 12,
                    "inchi_key": "ABCDEFGHIJKLMN-UHFFFAOYSA-N",
                    "mol_formula": "C9H8O5",
                    "mol_weight": 196.0,
                },
                {
                    "chemical_id": "3",
                    "name": "self",
                    "smiles": smiles,
                    "similarity": "1.0",
                    "global_frequency": 100,
                },
            ],
            "search_hash": "hash-1",
            "skipped_reason": None,
        }

    def _fake_docs(ids, **kwargs):
        return [
            {
                "docId": "CN-102942594-A",
                "pa": "Acme",
                "metadata": {
                    "pd": "2013-01-01",
                    "titles": [{"lang": "en", "titles": ["Aspirin complex patent"]}],
                },
            }
        ]

    monkeypatch.setattr(surechembl_client, "structure_search", _fake_search)
    monkeypatch.setattr(surechembl_client, "documents_for_chemicals", _fake_docs)

    out = sa.fetch_surechembl_alternatives(
        material="aspirin",
        smiles="CC(=O)Oc1ccccc1C(=O)O",
        limit=5,
        threshold=85,
        attach_patents=True,
    )
    rows = out["surechembl"]
    assert len(rows) == 1
    assert rows[0]["name"] == "Useful Analog"
    assert rows[0]["chemical_id"] == "2"
    assert rows[0]["patents"]
    assert rows[0]["patents"][0]["doc_id"] == "CN-102942594-A"
    assert "patents.google.com" in (rows[0]["patents"][0]["url"] or "")
    assert out["surechembl_meta"]["search_hash"] == "hash-1"
    assert out["surechembl_meta"]["count"] == 1


def test_structure_search_parses_poll_results(monkeypatch):
    monkeypatch.setattr(surechembl_client, "surechembl_enabled", lambda: True)
    posts: list[tuple] = []

    def _fake_post(path, body=None, timeout=2.0):
        posts.append(path)
        assert path == "/search/structure"
        assert "StructureSearchRequest" in (body or {})
        return {"status": "OK", "data": {"hash": "abc-123"}}

    def _fake_get(path, timeout=2.0):
        if path.endswith("/status"):
            return {"status": "OK", "data": {"message": "Searching finished.", "resultCount": 2}}
        if "/results" in path:
            return {
                "status": "OK",
                "data": {
                    "results": {
                        "structures": [
                            {
                                "chemical_id": "99",
                                "name": "hit",
                                "smiles": "CCO",
                                "similarity": "0.9",
                            }
                        ]
                    }
                },
            }
        return None

    monkeypatch.setattr(surechembl_client, "_http_post_json", _fake_post)
    monkeypatch.setattr(surechembl_client, "_http_get_json", _fake_get)
    out = surechembl_client.structure_search("CCO", threshold=85, limit=5, budget_s=2.0)
    assert out["search_hash"] == "abc-123"
    assert len(out["hits"]) == 1
    assert out["hits"][0]["chemical_id"] == "99"

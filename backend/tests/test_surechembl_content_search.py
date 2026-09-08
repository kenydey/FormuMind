"""SureChEMBL content search (P2) — mocked offline CI."""
from __future__ import annotations

import io
import zipfile

import pytest

from app.domain.schemas import Evidence
from app.services import literature, surechembl_client


@pytest.fixture(autouse=True)
def _clear():
    surechembl_client.clear_surechembl_cache()
    yield
    surechembl_client.clear_surechembl_cache()


def test_content_search_normalizes_documents(monkeypatch):
    monkeypatch.setattr(surechembl_client, "surechembl_enabled", lambda: True)

    def _fake_post(path, body=None, timeout=2.0):
        assert path.startswith("/search/content?")
        assert "query=" in path
        assert "itemsPerPage=" in path
        return {
            "status": "OK",
            "data": {
                "type": "CONTENT",
                "results": {
                    "total_hits": 2,
                    "documents": [
                        {
                            "docId": "CN-104789083-B",
                            "pa": "ACME CO",
                            "metadata": {
                                "pd": "20170725",
                                "titles": [
                                    {
                                        "lang": "en",
                                        "titles": ["Quick-drying zinc phosphate epoxy primer"],
                                    }
                                ],
                            },
                        },
                        {
                            "docId": "US-20120129964-A1",
                            "pa": "null",
                            "metadata": {"pd": "null", "titles": []},
                        },
                    ],
                },
            },
        }

    monkeypatch.setattr(surechembl_client, "_http_post_json", _fake_post)
    rows = surechembl_client.content_search("epoxy zinc phosphate", limit=5)
    assert len(rows) == 2
    assert rows[0]["doc_id"] == "CN-104789083-B"
    assert "zinc phosphate" in rows[0]["title"].lower()
    assert rows[0]["assignee"] == "ACME CO"
    assert rows[0]["url"] and "patents.google.com" in rows[0]["url"]
    assert rows[1]["assignee"] is None
    assert rows[1]["pub_date"] is None


def test_document_chemistry_parses_zip_csv(monkeypatch):
    monkeypatch.setattr(surechembl_client, "surechembl_enabled", lambda: True)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "document_chemistry.csv",
            "id,chemical_id,name,smiles,global_frequency,mol_formula\n"
            "1,10,epoxy,C1CO1,71,C2H4O\n"
            "2,20,zinc phosphate,[Zn].OP(O)(O)=O,5,ZnH3O4P\n",
        )

    monkeypatch.setattr(surechembl_client, "_http_post_bytes", lambda *a, **k: buf.getvalue())
    rows = surechembl_client.document_chemistry("CN-104789083-B", limit=5)
    assert len(rows) == 2
    assert rows[0]["name"] == "epoxy"
    assert rows[0]["smiles"] == "C1CO1"


def test_search_surechembl_content_builds_evidence(monkeypatch):
    monkeypatch.setattr(
        surechembl_client,
        "content_search",
        lambda q, limit=20, offset=0, timeout=12.0: [
            {
                "doc_id": "CN-104789083-B",
                "title": "Epoxy zinc primer",
                "assignee": "Acme",
                "pub_date": "20170725",
                "url": "https://patents.google.com/patent/CN104789083B",
                "surechembl_url": "https://www.surechembl.org/document/CN-104789083-B",
            }
        ],
    )
    monkeypatch.setattr(
        surechembl_client,
        "document_chemistry",
        lambda doc_id, limit=12, timeout=12.0: [
            {"name": "epoxy", "smiles": "C1CO1", "chemical_id": "1", "global_frequency": 10}
        ],
    )
    monkeypatch.setattr(surechembl_client, "surechembl_enabled", lambda: True)

    rows = literature.search_surechembl_content("epoxy zinc", limit=5, attach_chemistry=True)
    assert len(rows) == 1
    assert isinstance(rows[0], Evidence)
    assert rows[0].source == "surechembl"
    assert rows[0].identifier == "CN-104789083-B"
    assert rows[0].url and "patents.google.com" in rows[0].url
    assert "chemistry: epoxy" in rows[0].snippet


def test_build_streams_includes_surechembl(monkeypatch):
    monkeypatch.setattr(literature, "search_surechembl_content", lambda *a, **k: [])
    streams = literature._build_streams(
        "epoxy zinc",
        "epoxy zinc",
        ["surechembl"],
        None,
        10,
    )
    names = [s["name"] for s in streams]
    assert "surechembl" in names
    assert "patents" not in names


def test_get_source_availability_includes_surechembl():
    status = literature.get_source_availability()
    assert "surechembl" in status
    assert "available" in status["surechembl"]

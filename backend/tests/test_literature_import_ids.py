"""Wave E-Lit — DOI/ChemRxiv import-ids."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import literature_import_ids as iid
from app.services import literature_manifest as lm


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(lm, "_data_root", lambda: tmp_path)
    return tmp_path


class _S:
    literature_manifest_enabled = True
    literature_library_enabled = True


def test_parse_rejects_arxiv_and_pmid():
    parsed = iid.parse_import_tokens(
        "arxiv:2301.12345 pmid:12345 10.26434/chemrxiv-2024-abcd"
    )
    reasons = {f["detail"] for f in parsed["failures"]}
    assert "arxiv" in reasons
    assert "pmid" in reasons
    assert any("chemrxiv" in d.lower() for d in parsed["dois"])


def test_import_ids_skip_duplicate(tmp_data, monkeypatch):
    monkeypatch.setattr(
        iid,
        "resolve_doi_item",
        lambda doi: lm.ensure_item_library_fields(
            {
                "id": f"doi:{doi.lower()}",
                "title": "Paper",
                "doi": doi.lower(),
                "source": "import_ids",
                "snippet": "",
                "evidence_class": "import_ids",
                "screening": "unset",
            }
        ),
    )
    r1 = iid.import_ids("p1", "10.1000/AAA", settings=_S())
    assert len(r1["added"]) == 1
    r2 = iid.import_ids("p1", "10.1000/aaa", settings=_S())
    assert r2["added"] == []
    assert r2["skipped"]


def test_import_chemrxiv_url(tmp_data, monkeypatch):
    cid = "11111111-2222-3333-4444-555555555555"

    def fake_resolve(chemrxiv_id: str):
        assert chemrxiv_id == cid
        return lm.ensure_item_library_fields(
            {
                "id": f"chemrxiv:{cid}",
                "title": "Chem preprint",
                "chemrxiv_id": cid,
                "doi": None,
                "source": "ChemRxiv",
                "snippet": "",
                "evidence_class": "import_ids",
                "screening": "unset",
            }
        )

    monkeypatch.setattr(iid, "resolve_chemrxiv_item", fake_resolve)
    text = f"https://chemrxiv.org/engage/chemrxiv/article-details/{cid}"
    res = iid.import_ids("p1", text, settings=_S())
    assert res["added"]
    man = lm.load_manifest("p1")
    assert man["items"][0]["chemrxiv_id"] == cid


def test_arxiv_token_not_imported(tmp_data, monkeypatch):
    monkeypatch.setattr(
        iid,
        "resolve_doi_item",
        lambda doi: (_ for _ in ()).throw(AssertionError("should not resolve")),
    )
    res = iid.import_ids("p1", "arxiv:2301.12345", settings=_S())
    assert res["added"] == []
    assert any(f["reason"] == "unsupported_scheme" for f in res["failures"])
    assert lm.load_manifest("p1")["items"] == []

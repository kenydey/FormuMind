"""Wave E-Lit — BibTeX / RIS roundtrip."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import literature_citation_io as cio
from app.services import literature_manifest as lm


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(lm, "_data_root", lambda: tmp_path)
    return tmp_path


class _S:
    literature_manifest_enabled = True
    literature_library_enabled = True


def _seed_chem():
    man = lm.empty_manifest("p1")
    man["items"] = [
        lm.ensure_item_library_fields(
            {
                "id": "doi:10.26434/chemrxiv-2024-demo",
                "title": "Demo ChemRxiv Paper",
                "doi": "10.26434/chemrxiv-2024-demo",
                "chemrxiv_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "authors": ["Ada Lovelace", "Grace Hopper"],
                "year": 2024,
                "url": "https://chemrxiv.org/engage/chemrxiv/article-details/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "notes": "lab note",
                "screening": "match",
                "source": "ChemRxiv",
            }
        )
    ]
    return lm.save_manifest(man)


def test_bibtex_roundtrip(tmp_data, monkeypatch):
    _seed_chem()
    bib = cio.export_bibtex("p1", scope="library", settings=_S())
    assert "eprinttype = {chemrxiv}" in bib
    assert "10.26434/chemrxiv-2024-demo" in bib

    # Avoid network resolve on import
    monkeypatch.setattr(
        "app.services.literature_import_ids.resolve_doi_item",
        lambda doi: lm.ensure_item_library_fields(
            {
                "id": f"doi:{doi}",
                "title": "Demo ChemRxiv Paper",
                "doi": doi,
                "chemrxiv_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "source": "ChemRxiv",
                "screening": "unset",
            }
        ),
    )
    # Clear and re-import
    man = lm.empty_manifest("p1")
    lm.save_manifest(man)
    res = cio.import_citation("p1", format="bibtex", text=bib, settings=_S())
    assert res["added"]
    assert res["failures"] == []
    again = cio.import_citation("p1", format="bibtex", text=bib, settings=_S())
    assert again["added"] == []
    assert again["skipped"]


def test_ris_export_and_arxiv_rejected(tmp_data):
    _seed_chem()
    ris = cio.export_ris("p1", scope="library", settings=_S())
    assert "TY  - PREPRINT" in ris
    assert "ChemRxiv:" in ris
    items, failures = cio.parse_ris(
        "TY  - JOUR\nTI  - Bad\nUR  - https://arxiv.org/abs/2301.12345\nER  - \n"
    )
    assert items == []
    assert failures and failures[0]["detail"] == "arxiv"


def test_frozen_scope_count(tmp_data):
    _seed_chem()
    man = lm.load_manifest("p1")
    man["items"].append(
        lm.ensure_item_library_fields(
            {
                "id": "extra",
                "title": "Not frozen",
                "doi": "10.1/extra",
                "screening": "unset",
            }
        )
    )
    lm.save_manifest(man)
    lm.freeze("p1", item_ids=["doi:10.26434/chemrxiv-2024-demo"], actor="t", settings=_S())
    bib = cio.export_bibtex("p1", scope="frozen", settings=_S())
    assert bib.count("@article") == 1

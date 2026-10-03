"""Wave E-Lit — duplicates + merge."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import literature_duplicates as dup
from app.services import literature_manifest as lm


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(lm, "_data_root", lambda: tmp_path)
    return tmp_path


class _S:
    literature_manifest_enabled = True
    literature_library_enabled = True


def test_identifier_duplicate_group():
    items = [
        lm.ensure_item_library_fields(
            {"id": "a", "title": "One", "doi": "10.1/x", "screening": "unset"}
        ),
        lm.ensure_item_library_fields(
            {"id": "b", "title": "Two", "doi": "10.1/x", "screening": "match", "tags": ["t"]}
        ),
    ]
    groups = dup.find_duplicate_groups(items)
    assert len(groups) == 1
    assert groups[0]["match"] == "identifier"
    assert set(groups[0]["item_ids"]) == {"a", "b"}


def test_metadata_duplicate_group():
    items = [
        lm.ensure_item_library_fields(
            {
                "id": "a",
                "title": "Epoxy Coatings Review",
                "authors": ["Zhang, Li"],
                "year": 2020,
                "screening": "unset",
            }
        ),
        lm.ensure_item_library_fields(
            {
                "id": "b",
                "title": "epoxy coatings review",
                "authors": ["Li Zhang"],
                "year": 2020,
                "screening": "unset",
            }
        ),
    ]
    groups = dup.find_duplicate_groups(items)
    assert len(groups) == 1
    assert groups[0]["match"] == "metadata"


def test_merge_remaps_freeze_and_tags(tmp_data):
    man = lm.empty_manifest("p1")
    man["items"] = [
        lm.ensure_item_library_fields(
            {
                "id": "a",
                "title": "Thin",
                "doi": "10.1/x",
                "screening": "unset",
                "tags": ["a"],
            }
        ),
        lm.ensure_item_library_fields(
            {
                "id": "b",
                "title": "Rich title about epoxy",
                "doi": "10.1/x",
                "screening": "match",
                "tags": ["b"],
                "authors": ["Ada"],
                "year": 2021,
                "has_fulltext": True,
            }
        ),
    ]
    lm.save_manifest(man)
    lm.freeze("p1", item_ids=["a", "b"], actor="t", settings=_S())
    res = dup.merge_items("p1", ["a", "b"], settings=_S())
    assert res["survivor_id"] == "b"
    man = res["manifest"]
    assert len(man["items"]) == 1
    assert set(man["items"][0]["tags"]) == {"a", "b"}
    assert man["items"][0]["screening"] == "match"
    assert man["frozen"] is not None
    assert man["frozen"]["item_ids"] == ["b"]

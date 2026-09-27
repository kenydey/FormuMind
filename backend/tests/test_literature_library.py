"""Wave E-Lit — literature library list/patch/collections."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import literature_manifest as lm


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(lm, "_data_root", lambda: tmp_path)
    return tmp_path


class _S:
    literature_manifest_enabled = True
    literature_library_enabled = True


class _SLibOff:
    literature_manifest_enabled = True
    literature_library_enabled = False


def _seed() -> None:
    man = lm.empty_manifest("p1")
    man["items"] = [
        lm.ensure_item_library_fields(
            {
                "id": "a",
                "title": "Epoxy coating",
                "doi": "10.1/aaa",
                "source": "lit",
                "snippet": "adhesion",
                "evidence_class": "search_hit",
                "screening": "unset",
                "tags": ["epoxy"],
                "notes": "keep me",
            }
        ),
        lm.ensure_item_library_fields(
            {
                "id": "b",
                "title": "Silane primer",
                "doi": "10.1/bbb",
                "source": "lit",
                "snippet": "coupling",
                "evidence_class": "search_hit",
                "screening": "match",
            }
        ),
    ]
    lm.save_manifest(man)


def test_schema_migrate_adds_collections(tmp_data):
    path = lm.manifest_path("legacy")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"project_id":"legacy","schema_version":1,"items":[{"id":"x","title":"T","screening":"unset"}],'
        '"retrievals":[],"events":[],"frozen":null,"coverage":{}}',
        encoding="utf-8",
    )
    man = lm.load_manifest("legacy")
    assert man["schema_version"] == 2
    assert man["collections"] == []
    assert man["items"][0]["tags"] == []
    assert "identifiers" in man["items"][0]


def test_list_library_filters(tmp_data):
    _seed()
    out = lm.list_library("p1", q="epoxy", settings=_S())
    assert [i["id"] for i in out["items"]] == ["a"]
    out2 = lm.list_library("p1", tag="epoxy", settings=_S())
    assert [i["id"] for i in out2["items"]] == ["a"]
    out3 = lm.list_library("p1", screening="match", settings=_S())
    assert [i["id"] for i in out3["items"]] == ["b"]


def test_library_flag_gate(tmp_data):
    _seed()
    with pytest.raises(PermissionError, match="literature_library_enabled"):
        lm.list_library("p1", settings=_SLibOff())


def test_patch_notes_keeps_freeze(tmp_data):
    _seed()
    lm.freeze("p1", item_ids=["a", "b"], actor="t", settings=_S())
    man = lm.patch_library_item(
        "p1", "a", {"notes": "updated note"}, settings=_S(), require_library=True
    )
    assert man["frozen"] is not None
    assert man["items"][0]["notes"] == "updated note" or any(
        i["id"] == "a" and i["notes"] == "updated note" for i in man["items"]
    )


def test_patch_doi_clears_freeze(tmp_data):
    _seed()
    lm.freeze("p1", item_ids=["a"], actor="t", settings=_S())
    man = lm.patch_library_item(
        "p1", "a", {"doi": "10.9/new"}, settings=_S(), require_library=True
    )
    assert man["frozen"] is None


def test_screening_only_without_library_flag(tmp_data):
    _seed()
    man = lm.patch_library_item(
        "p1",
        "a",
        {"screening": "match"},
        settings=_SLibOff(),
        require_library=False,
    )
    assert any(i["id"] == "a" and i["screening"] == "match" for i in man["items"])


def test_collections_crud(tmp_data):
    _seed()
    man = lm.create_collection("p1", "Coatings", settings=_S())
    cid = man["collections"][0]["id"]
    man = lm.patch_collection("p1", cid, item_ids=["a", "b"], settings=_S())
    assert set(man["collections"][0]["item_ids"]) == {"a", "b"}
    item_a = next(i for i in man["items"] if i["id"] == "a")
    assert cid in item_a["collection_ids"]
    man = lm.delete_collection("p1", cid, settings=_S())
    assert man["collections"] == []
    item_a = next(i for i in man["items"] if i["id"] == "a")
    assert cid not in item_a["collection_ids"]

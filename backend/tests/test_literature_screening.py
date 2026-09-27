"""Tests for light literature screening."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import literature_manifest as lm
from app.services import literature_screening as ls


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(lm, "_data_root", lambda: tmp_path)
    return tmp_path


class _S:
    literature_manifest_enabled = True
    literature_screening_enabled = True
    screening_auto_freeze = False


def _seed():
    man = lm.empty_manifest("p2")
    man["items"] = [
        {
            "id": "1",
            "title": "Waterborne epoxy coating adhesion",
            "doi": "10.1/x",
            "snippet": "VOC low",
            "evidence_class": "search_hit",
            "screening": "unset",
        },
        {
            "id": "2",
            "title": "Mouse genome study",
            "doi": None,
            "snippet": "biology",
            "evidence_class": "search_hit",
            "screening": "unset",
        },
        {
            "id": "3",
            "title": "Epoxy primer review",
            "doi": "10.1/y",
            "snippet": "corrosion",
            "evidence_class": "search_hit",
            "screening": "unset",
        },
    ]
    lm.save_manifest(man)


def test_classify_include_exclude():
    item = {"title": "epoxy coating", "snippet": "adhesion", "doi": "10.1/z"}
    assert (
        ls.classify_item(
            item, include_keywords=["epoxy"], exclude_keywords=["mouse"]
        )
        == "match"
    )
    assert (
        ls.classify_item(
            item, include_keywords=["epoxy"], exclude_keywords=["coating"]
        )
        == "no_match"
    )
    assert (
        ls.classify_item(
            {"title": "other", "snippet": "", "doi": None},
            include_keywords=["epoxy"],
            exclude_keywords=[],
            require_doi=True,
        )
        == "uncertain"
    )


def test_screen_apply_and_override(tmp_data):
    _seed()
    out = ls.screen_project(
        "p2",
        {"include_keywords": ["epoxy"], "exclude_keywords": ["mouse"]},
        apply=True,
        settings=_S(),
    )
    assert out["summary"]["match"] >= 1
    assert out["summary"]["no_match"] >= 1
    man = lm.load_manifest("p2")
    by = {i["id"]: i["screening"] for i in man["items"]}
    assert by["2"] == "no_match"
    lm.update_item_screening("p2", "2", "match")
    assert lm.load_manifest("p2")["items"][1]["screening"] == "match" or any(
        i["id"] == "2" and i["screening"] == "match" for i in lm.load_manifest("p2")["items"]
    )


def test_auto_freeze(tmp_data):
    _seed()

    class Auto(_S):
        screening_auto_freeze = True

    out = ls.screen_project(
        "p2",
        {"include_keywords": ["epoxy"], "exclude_keywords": []},
        apply=True,
        settings=Auto(),
    )
    man = out["manifest"]
    assert man.get("frozen")
    assert all(
        i in {"1", "3"} for i in man["frozen"]["item_ids"]
    )


def test_screening_disabled(tmp_data):
    class Off(_S):
        literature_screening_enabled = False

    with pytest.raises(PermissionError):
        ls.screen_project("p2", {}, settings=Off())

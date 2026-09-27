"""Tests for literature manifest / frozen corpus."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import literature_manifest as lm
from app.services import publication_preflight as pf


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(lm, "_data_root", lambda: tmp_path)
    monkeypatch.setattr(pf, "_data_root", lambda: tmp_path)
    return tmp_path


class _S:
    literature_manifest_enabled = True
    frozen_corpus_required_for_export = False
    literature_screening_required_for_export = False
    publication_preflight_enabled = True


def _seed(project_id: str = "p1") -> dict:
    man = lm.empty_manifest(project_id)
    man["items"] = [
        {
            "id": "a",
            "title": "Epoxy coating",
            "doi": "10.1/aaa",
            "source": "lit",
            "snippet": "adhesion",
            "evidence_class": "search_hit",
            "screening": "unset",
        },
        {
            "id": "b",
            "title": "Unrelated biology",
            "doi": None,
            "source": "lit",
            "snippet": "mouse",
            "evidence_class": "search_hit",
            "screening": "no_match",
        },
        {
            "id": "c",
            "title": "Silane primer",
            "doi": "10.1/ccc",
            "source": "lit",
            "snippet": "coupling",
            "evidence_class": "search_hit",
            "screening": "match",
        },
    ]
    return lm.save_manifest(man)


def test_freeze_digest_stable(tmp_data):
    _seed()
    m1 = lm.freeze("p1", item_ids=["a", "c"], actor="alice", settings=_S())
    d1 = m1["frozen"]["digest"]
    m2 = lm.freeze("p1", item_ids=["c", "a"], actor="alice", settings=_S())
    assert m2["frozen"]["digest"] == d1
    assert m2["coverage"]["frozen_count"] == 2


def test_default_freeze_prefers_match(tmp_data):
    _seed()
    m = lm.freeze("p1", actor="bob", settings=_S())
    assert set(m["frozen"]["item_ids"]) == {"c"}


def test_literature_slice_from_frozen(tmp_data):
    _seed()
    lm.freeze("p1", item_ids=["a"], actor="x", settings=_S())
    sl = lm.literature_slice_from_frozen("p1")
    assert sl and sl["frozen"] is True
    assert sl["source_ids"] == ["a"]


def test_preflight_requires_frozen_when_flag(tmp_data):
    class Req(_S):
        frozen_corpus_required_for_export = True

    findings = lm.preflight_corpus_findings("p1", "clean text", settings=Req())
    assert any(f["check"] == "corpus" for f in findings)

    _seed()
    lm.freeze("p1", item_ids=["a"], actor="x", settings=_S())
    findings2 = lm.preflight_corpus_findings("p1", "text", settings=Req())
    assert not any(f["check"] == "corpus" for f in findings2)


def test_preflight_corpus_cite(tmp_data):
    _seed()
    lm.freeze("p1", item_ids=["a"], actor="x", settings=_S())
    findings = lm.preflight_corpus_findings(
        "p1",
        "See 10.1/ccc for more.",
        settings=_S(),
        cite_source_ids=["b"],
    )
    checks = {f["check"] for f in findings}
    assert "corpus_cite" in checks


def test_export_allowed_blocks_without_freeze(tmp_data):
    class Req:
        publication_preflight_enabled = True
        literature_manifest_enabled = True
        frozen_corpus_required_for_export = True
        literature_screening_required_for_export = False

    ok, detail = pf.export_allowed("p1", "storm", "Some clean prose.", settings=Req())
    assert ok is False
    state = detail.get("state") or {}
    assert any(
        f.get("check") == "corpus" and f.get("status") == "open"
        for f in (state.get("findings") or [])
    )


# ── W1-5（P0-8）回归测试 ─────────────────────────────────────────


def test_update_item_screening_marks_human(tmp_data):
    """P0-8：update_item_screening 标记 source=human + by/at，旧调用兼容。"""
    _seed("p7")
    out = lm.update_item_screening("p7", "a", "match", actor="alice")
    item = next(i for i in out["items"] if i["id"] == "a")
    assert item["screening"] == "match"
    assert item["screening_source"] == "human"
    assert item["screening_by"] == "alice"
    assert item["screening_at"]
    # 旧签名（不传 actor）仍可用，默认 actor=user
    lm.update_item_screening("p7", "a", "uncertain")
    item2 = next(i for i in lm.load_manifest("p7")["items"] if i["id"] == "a")
    assert item2["screening_source"] == "human"
    assert item2["screening_by"] == "user"

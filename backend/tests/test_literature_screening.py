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


# ── W1-5（P0-4/P0-7/P0-8）回归测试 ───────────────────────────────


def test_short_circuit_insufficient_evidence():
    """P0-4 短路三条件：无 title / 无 snippet+abstract → uncertain + 标记。"""
    # 1. 无 title
    disp, model = ls._classify_with_model(
        {"title": "  ", "snippet": "epoxy"},
        include_keywords=["epoxy"],
        exclude_keywords=[],
    )
    assert disp == "uncertain"
    assert model == "insufficient-evidence"
    # 2. 有 title 但无 snippet 也无 abstract
    disp, model = ls._classify_with_model(
        {"title": "epoxy coating", "snippet": "", "doi": "10.1/z"},
        include_keywords=["epoxy"],
        exclude_keywords=[],
    )
    assert disp == "uncertain"
    assert model == "insufficient-evidence"
    # 3. 有 title + abstract（无 snippet）不走短路，正常命中
    disp, model = ls._classify_with_model(
        {"title": "epoxy coating", "abstract": "adhesion test"},
        include_keywords=["epoxy"],
        exclude_keywords=[],
    )
    assert disp == "match"
    assert model is None
    # 旧签名保持 str 返回
    assert (
        ls.classify_item(
            {"title": ""}, include_keywords=[], exclude_keywords=[]
        )
        == "uncertain"
    )


def test_screen_project_marks_insufficient_evidence_decision(tmp_data):
    """P0-4：screen_project 的 decision 带 model=insufficient-evidence。"""
    man = lm.empty_manifest("p9")
    man["items"] = [
        {"id": "a", "title": "", "snippet": "x", "screening": "unset"},
        {"id": "b", "title": "t", "snippet": "", "screening": "unset"},
    ]
    lm.save_manifest(man)
    out = ls.screen_project(
        "p9", {"include_keywords": ["epoxy"]}, apply=True, settings=_S()
    )
    by = {d["id"]: d for d in out["decisions"]}
    assert by["a"]["screening"] == "uncertain"
    assert by["a"]["model"] == "insufficient-evidence"
    assert by["b"]["screening"] == "uncertain"
    assert by["b"]["model"] == "insufficient-evidence"


def test_screen_project_skips_unchanged_digest(tmp_data, monkeypatch):
    """P0-7：二次 screen_project digest 未变且规则未变 → 跳过，不调 classify。"""
    _seed()
    criteria = {"include_keywords": ["epoxy"], "exclude_keywords": ["mouse"]}
    out1 = ls.screen_project("p2", criteria, apply=True, settings=_S())
    assert out1["applied"]
    # 规则版本落盘到 manifest 顶层
    assert lm.load_manifest("p2").get("screening_rule_version")

    calls: list = []
    orig = ls._classify_with_model

    def spy(item, **kw):
        calls.append(item.get("id"))
        return orig(item, **kw)

    monkeypatch.setattr(ls, "_classify_with_model", spy)
    out2 = ls.screen_project("p2", criteria, apply=True, settings=_S())
    assert calls == []
    assert all(d.get("skipped_digest") for d in out2["decisions"])
    assert out2["summary"] == out1["summary"]

    # 改一条 title → digest 变化 → 只重筛这一条
    man = lm.load_manifest("p2")
    man["items"][0]["title"] = "Waterborne epoxy coating adhesion v2"
    lm.save_manifest(man)
    out3 = ls.screen_project("p2", criteria, apply=True, settings=_S())
    assert calls == ["1"]
    by3 = {d["id"]: d for d in out3["decisions"]}
    assert not by3["1"].get("skipped_digest")
    assert by3["2"].get("skipped_digest")
    assert by3["3"].get("skipped_digest")


def test_screen_project_digest_skip_on_rule_change(tmp_data):
    """P0-7：规则变化（include 关键字不同）→ 不跳过，全部重筛。"""
    _seed()
    ls.screen_project(
        "p2",
        {"include_keywords": ["epoxy"], "exclude_keywords": ["mouse"]},
        apply=True,
        settings=_S(),
    )
    out = ls.screen_project(
        "p2",
        {"include_keywords": ["polyurethane"], "exclude_keywords": ["mouse"]},
        apply=True,
        settings=_S(),
    )
    assert not any(d.get("skipped_digest") for d in out["decisions"])
    # 规则版本已更新
    assert lm.load_manifest("p2")["screening_rule_version"] == ls._screening_rule_version(
        {"include_keywords": ["polyurethane"], "exclude_keywords": ["mouse"]}
    )


def test_human_override_not_overwritten(tmp_data):
    """P0-8：人工 override 不被后续 screen_project 覆盖。"""
    _seed()
    ls.screen_project(
        "p2",
        {"include_keywords": ["epoxy"], "exclude_keywords": []},
        apply=True,
        settings=_S(),
    )
    lm.update_item_screening("p2", "1", "no_match", actor="tester")
    item = next(i for i in lm.load_manifest("p2")["items"] if i["id"] == "1")
    assert item["screening_source"] == "human"
    assert item["screening_by"] == "tester"
    assert item["screening_at"]

    # criteria 本会判成 match，但 human 保护跳过
    out = ls.screen_project(
        "p2",
        {"include_keywords": ["epoxy"], "exclude_keywords": []},
        apply=True,
        settings=_S(),
    )
    by = {d["id"]: d for d in out["decisions"]}
    assert by["1"]["screening"] == "no_match"
    assert by["1"].get("skipped_human") is True
    kept = next(i for i in lm.load_manifest("p2")["items"] if i["id"] == "1")
    assert kept["screening"] == "no_match"
    assert kept["screening_source"] == "human"


def test_human_override_force_overwrites(tmp_data):
    """P0-8：criteria 显式 force=True → 覆盖人工判定，source 回 heuristic。"""
    _seed()
    ls.screen_project(
        "p2",
        {"include_keywords": ["epoxy"], "exclude_keywords": []},
        apply=True,
        settings=_S(),
    )
    lm.update_item_screening("p2", "1", "no_match", actor="tester")
    out = ls.screen_project(
        "p2",
        {"include_keywords": ["epoxy"], "exclude_keywords": [], "force": True},
        apply=True,
        settings=_S(),
    )
    by = {d["id"]: d for d in out["decisions"]}
    assert by["1"]["screening"] == "match"
    assert "skipped_human" not in by["1"]
    item = next(i for i in lm.load_manifest("p2")["items"] if i["id"] == "1")
    assert item["screening"] == "match"
    assert item["screening_source"] == "heuristic"

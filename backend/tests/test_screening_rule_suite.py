"""Tests for W6-2 screening rule suite: presets, named versions, rollback,
evaluate, async batch job. P0-7/P0-8 behaviour must stay intact."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.services import literature_manifest as lm
from app.services import literature_screening as ls
from app.services import screening_presets as sp


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(lm, "_data_root", lambda: tmp_path)
    monkeypatch.setenv("FORMUMIND_TASK_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("FORMUMIND_TASK_PROGRESS_DIR", str(tmp_path / "progress"))
    return tmp_path


class _S:
    literature_manifest_enabled = True
    literature_screening_enabled = True
    screening_auto_freeze = False
    screening_async_threshold = 500
    celery_eager = True


def _seed(pid: str = "p1"):
    man = lm.empty_manifest(pid)
    man["items"] = [
        {
            "id": "1",
            "title": "Waterborne epoxy coating for corrosion protection",
            "doi": "10.1/x",
            "snippet": "zinc-rich primer adhesion",
            "evidence_class": "search_hit",
            "screening": "unset",
        },
        {
            "id": "2",
            "title": "Mouse genome sequencing study",
            "doi": None,
            "snippet": "biology",
            "evidence_class": "search_hit",
            "screening": "unset",
        },
        {
            "id": "3",
            "title": "US patent 12345 coating composition",
            "doi": None,
            "snippet": "patent claims",
            "evidence_class": "search_hit",
            "screening": "unset",
        },
    ]
    lm.save_manifest(man)


# ── 1. presets ─────────────────────────────────────────────────────────────


def test_presets_list_and_get():
    presets = sp.list_presets()
    names = {p["name"] for p in presets}
    assert {
        "anticorrosion_coating",
        "metal_pretreatment",
        "conversion_coating",
        "waterborne_coating",
        "patents_only",
        "exclude_patents",
    } <= names
    assert sp.get_preset("nope") is None
    crit = sp.get_preset("patents_only")
    assert crit is not None and "patent" in crit["include_keywords"]


def test_merge_preset_unknown_raises():
    with pytest.raises(ValueError):
        sp.merge_preset("nope", {})
    # None preset → criteria untouched
    assert sp.merge_preset(None, {"include_keywords": ["a"]}) == {
        "include_keywords": ["a"]
    }


def test_merge_preset_explicit_overrides():
    merged = sp.merge_preset(
        "anticorrosion_coating",
        {"include_keywords": ["epoxy"], "require_doi": True, "year_min": 2020},
    )
    assert merged["include_keywords"] == ["epoxy"]
    assert merged["require_doi"] is True
    assert merged["year_min"] == 2020
    # preset defaults fill the gaps
    assert "food" in merged["exclude_keywords"]
    assert merged["year_max"] is None


def test_screen_project_with_preset(tmp_data, monkeypatch):
    _seed()
    res = ls.screen_project(
        "p1", {}, apply=True, settings=_S(), preset="exclude_patents"
    )
    by_id = {d["id"]: d["screening"] for d in res["decisions"]}
    assert by_id["3"] == "no_match"  # 专利被排除
    assert by_id["1"] == "match"
    man = lm.load_manifest("p1")
    assert man["screening_preset"] == "exclude_patents"
    assert man["screening_criteria"]["exclude_keywords"] == ["patent", "专利"]


def test_screen_project_unknown_preset_raises(tmp_data):
    _seed()
    with pytest.raises(ValueError):
        ls.screen_project("p1", {}, settings=_S(), preset="nope")


# ── 2. named versions + history + diff + rollback ────────────────────────────


def test_save_list_rule_versions(tmp_data):
    _seed()
    r1 = ls.save_rule_version(
        "p1", name="v-coating", criteria={"include_keywords": ["epoxy"]},
        created_by="cheng", changelog="首版",
    )
    r2 = ls.save_rule_version(
        "p1", name="v-coating", criteria={"include_keywords": ["epoxy", "zinc"]},
        changelog="加富锌",
    )
    assert r1["version"] != r2["version"]
    assert r1["created_by"] == "cheng"
    out = ls.list_rule_versions("p1", settings=_S())
    assert len(out["versions"]) == 2
    assert out["versions"][0]["version"] == r2["version"]  # 新est first
    assert len(out["history"]) == 2
    assert all(h["action"] == "saved" for h in out["history"])


def test_versions_saved_within_one_clock_tick_still_list_newest_first(tmp_data, monkeypatch):
    """Windows' clock ticks every ~15.6 ms: two saves inside one tick share ``created_at``.

    The sort used to be stable on that key alone, so the *older* version came first (and a rollback "to the latest
    version of <name>" picked the older one). A frozen clock reproduces the tie on every platform.
    """
    _seed()
    monkeypatch.setattr(ls.time, "time", lambda: 1_700_000_000.0)
    r1 = ls.save_rule_version("p1", name="v", criteria={"include_keywords": ["epoxy"]})
    r2 = ls.save_rule_version("p1", name="v", criteria={"include_keywords": ["epoxy", "zinc"]})
    assert r1["created_at"] == r2["created_at"]
    out = ls.list_rule_versions("p1", settings=_S())
    assert [v["version"] for v in out["versions"]] == [r2["version"], r1["version"]]
    res = ls.rollback_rule_version("p1", name="v", settings=_S())
    assert res["rolled_back_to"]["version"] == r2["version"]


def test_save_rule_version_requires_name(tmp_data):
    _seed()
    with pytest.raises(ValueError):
        ls.save_rule_version("p1", name="  ", criteria={})


def test_diff_rule_versions():
    old = {
        "include_keywords": ["epoxy", "zinc"],
        "exclude_keywords": ["food"],
        "require_doi": False,
        "year_min": 2020,
        "year_max": None,
    }
    new = {
        "include_keywords": ["epoxy", "polyurethane"],
        "exclude_keywords": [],
        "require_doi": True,
        "year_min": 2020,
        "year_max": 2025,
    }
    d = ls.diff_rule_versions(old, new)
    assert d["include_added"] == ["polyurethane"]
    assert d["include_removed"] == ["zinc"]
    assert d["exclude_removed"] == ["food"]
    fields = {c["field"]: (c["old"], c["new"]) for c in d["threshold_changes"]}
    assert fields["require_doi"] == (False, True)
    assert fields["year_max"] == (None, 2025)
    assert "year_min" not in fields
    assert d["changed"] is True
    assert ls.diff_rule_versions(old, old)["changed"] is False


def test_rollback_rule_version(tmp_data):
    _seed()
    # v1：宽松 → 1,3 命中
    ls.screen_project(
        "p1", {"include_keywords": ["coating", "patent"]},
        apply=True, settings=_S(), rule_name="loose",
    )
    # v2：严格 → 只有 1 命中
    ls.screen_project(
        "p1", {"include_keywords": ["zinc-rich"]},
        apply=True, settings=_S(), rule_name="strict",
    )
    ls.save_rule_version(
        "p1", name="loose", criteria={"include_keywords": ["coating", "patent"]}
    )
    ls.save_rule_version(
        "p1", name="strict", criteria={"include_keywords": ["zinc-rich"]}
    )
    res = ls.rollback_rule_version("p1", name="loose", actor="cheng", settings=_S())
    assert res["rolled_back_to"]["name"] == "loose"
    by_id = {d["id"]: d["screening"] for d in res["decisions"]}
    assert by_id["1"] == "match" and by_id["3"] == "match"
    out = ls.list_rule_versions("p1", settings=_S())
    assert out["history"][-1]["action"] == "rolled_back"
    assert out["current_rule_name"] == "loose"
    with pytest.raises(LookupError):
        ls.rollback_rule_version("p1", name="missing", settings=_S())


def test_rollback_preserves_human_protection(tmp_data):
    _seed()
    man = lm.load_manifest("p1")
    man["items"][0]["screening"] = "no_match"
    man["items"][0]["screening_source"] = "human"
    lm.save_manifest(man)
    ls.save_rule_version(
        "p1", name="base", criteria={"include_keywords": ["coating"]}
    )
    res = ls.rollback_rule_version("p1", name="base", settings=_S())
    d1 = next(d for d in res["decisions"] if d["id"] == "1")
    assert d1.get("skipped_human") is True
    assert d1["screening"] == "no_match"  # P0-8：人工结论不受回滚覆盖


# ── 3. evaluate ────────────────────────────────────────────────────────────


def _seed_labeled(pid: str = "pe"):
    man = lm.empty_manifest(pid)
    # 6 条人工标注：4 正（epoxy 相关）+ 2 负
    items = [
        ("a1", "Epoxy coating corrosion test", "epoxy salt spray", "match"),
        ("a2", "Polyurethane topcoat weathering", "polyurethane UV", "match"),
        ("a3", "Zinc-rich primer adhesion", "zinc-rich steel", "match"),
        ("a4", "Waterborne epoxy primer", "水性 epoxy", "match"),
        ("b1", "Mouse genome study", "biology", "no_match"),
        ("b2", "Drug delivery nanoparticles", "pharma", "no_match"),
    ]
    man["items"] = [
        {
            "id": i, "title": t, "snippet": s, "doi": None,
            "evidence_class": "search_hit",
            "screening": lbl, "screening_source": "human",
        }
        for i, t, s, lbl in items
    ]
    lm.save_manifest(man)


def test_evaluate_insufficient_labeled(tmp_data):
    _seed()
    out = ls.evaluate_screening("p1", {"include_keywords": ["epoxy"]}, settings=_S())
    assert out["evaluated"] is False
    assert out["reason"] == "insufficient_labeled"


def test_evaluate_metrics_and_ablation(tmp_data):
    _seed_labeled()
    criteria = {"include_keywords": ["epoxy", "polyurethane", "zinc-rich"]}
    out = ls.evaluate_screening("pe", criteria, settings=_S())
    assert out["evaluated"] is True
    assert out["labeled_count"] == 6
    cm = out["confusion"]
    # a1,a2,a3,a4 命中（正例 4 全对）；b1,b2 无命中 → tp=4 fp=0 tn=2 fn=0
    assert cm == {"tp": 4, "fp": 0, "tn": 2, "fn": 0}
    assert out["metrics"]["precision"] == 1.0
    assert out["metrics"]["recall"] == 1.0
    # 消融：移除任一 include 关键词都会掉 recall（a1~a3 各依赖唯一关键词）
    abl = {r["keyword"]: r for r in out["include_ablation"]}
    assert abl["zinc-rich"]["recall_delta"] < 0
    assert abl["polyurethane"]["recall_delta"] < 0
    # 按 recall_delta 升序：最伤 recall 的排最前
    deltas = [r["recall_delta"] for r in out["include_ablation"]]
    assert deltas == sorted(deltas)


def test_evaluate_defaults_to_last_criteria(tmp_data):
    _seed_labeled()
    ls.screen_project(
        "pe", {"include_keywords": ["epoxy"]}, apply=True, settings=_S()
    )
    out = ls.evaluate_screening("pe", settings=_S())
    assert out["evaluated"] is True
    assert out["criteria"]["include_keywords"] == ["epoxy"]


# ── 4. async batch job ─────────────────────────────────────────────────────


def _patch_settings(monkeypatch):
    import app.config as cfg
    import app.worker.task_progress as tp

    monkeypatch.setattr(cfg, "get_settings", lambda: _S())
    monkeypatch.setattr(tp, "get_settings", lambda: _S())


def test_screening_async_threshold_default_and_settings():
    assert ls.screening_async_threshold(None) == 500
    assert ls.screening_async_threshold(_S()) == 500

    class _Low:
        screening_async_threshold = 7

    assert ls.screening_async_threshold(_Low()) == 7

    class _Bad:
        screening_async_threshold = "bad"

    assert ls.screening_async_threshold(_Bad()) == 500


def test_dispatch_screening_job_eager(tmp_data, monkeypatch):
    _patch_settings(monkeypatch)
    from app.worker import tasks as wt

    _seed()
    task_id = wt.dispatch_screening_job(
        "p1", {"include_keywords": ["coating"]}, preset=None, rule_name="job1"
    )
    assert task_id and task_id.startswith("screening-")
    deadline = time.time() + 15
    final = None
    while time.time() < deadline:
        final = wt.load_persisted_task(task_id)
        state = str(getattr(final, "state", "")) if final else ""
        if "completed" in state or "failed" in state:
            break
        time.sleep(0.2)
    assert final is not None, "job 未在 15s 内完成"
    assert "completed" in str(final.state), f"job failed: {final!r}"
    result = final.result or {}
    assert result["summary"]["match"] >= 1
    assert result["rule_name"] == "job1"
    # manifest 真实落盘
    man = lm.load_manifest("p1")
    assert man["screening_rule_name"] == "job1"


def test_screening_impl_direct(tmp_data, monkeypatch):
    _patch_settings(monkeypatch)
    from app.worker import tasks as wt

    _seed()
    seen: list[tuple[int, int]] = []
    # 直接测 impl 进度回调（不经过线程）
    import app.services.literature_screening as _ls

    res = _ls.screen_project(
        "p1", {"include_keywords": ["coating"]}, apply=True, settings=_S(),
        progress_cb=lambda d, t: seen.append((d, t)),
    )
    assert seen and seen[-1] == (3, 3)
    out = wt._screening_impl(
        "t-direct", {"project_id": "p1", "criteria": {"include_keywords": ["coating"]}}
    )
    assert out["summary"]["match"] == res["summary"]["match"]

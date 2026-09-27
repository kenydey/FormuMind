"""Unit tests for publication_preflight steel-stamp."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import publication_preflight as pf


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pf, "_data_root", lambda: tmp_path)
    return tmp_path


def test_citation_unbound_blocking(tmp_data):
    md = "Claim with [^1] and [^99].\n\n[^1]: Source A\n"
    findings = pf.run_checks(md, total_anchors=1)
    blocking = [f for f in findings if f.check == "citation" and f.severity == "blocking"]
    assert blocking
    assert any("99" in f.title or "99" in f.detail for f in blocking)


def test_placeholder_blocking(tmp_data):
    md = "Some text [citation needed] and TODO later.\n"
    findings = pf.run_checks(md)
    kinds = {f.check for f in findings}
    assert "placeholder" in kinds
    assert all(f.severity == "blocking" for f in findings if f.check == "placeholder")


def test_numeric_major_without_citation(tmp_data):
    md = "The coating cured at 120 °C for 2 h with 5 wt% additive.\n"
    findings = pf.run_checks(md)
    numeric = [f for f in findings if f.check == "numeric"]
    assert numeric
    assert all(f.severity == "major" for f in numeric)


def test_numeric_ok_with_nearby_citation(tmp_data):
    md = "Cured at 120 °C [^1] with good adhesion.\n\n[^1]: Paper\n"
    findings = pf.run_checks(md, total_anchors=1)
    numeric = [f for f in findings if f.check == "numeric"]
    assert not numeric


def test_review_override_finalize(tmp_data):
    md = "Text with [citation needed].\n"
    state = pf.review_markdown("proj-a", "storm", md)
    assert state["open_blocking"] >= 1
    fid = state["findings"][0]["id"]
    with pytest.raises(ValueError):
        pf.override_finding("proj-a", "storm", fid, actor="", reason="")
    state2 = pf.override_finding(
        "proj-a", "storm", fid, actor="alice", reason="placeholder accepted for draft"
    )
    assert all(
        f["status"] == "overridden"
        for f in state2["findings"]
        if f["id"] == fid
    )
    # Still may have other open findings — override all blocking
    for f in list(state2["findings"]):
        if f["severity"] == "blocking" and f["status"] == "open":
            pf.override_finding(
                "proj-a", "storm", f["id"], actor="alice", reason="ok"
            )
    ready = pf.assert_ready("proj-a", "storm", md, actor="alice")
    assert ready["ready"] is True
    assert ready["state"]["finalization"]


def test_export_allowed_blocks_then_allows(tmp_data):
    class S:
        publication_preflight_enabled = True

    md = "TODO fix this claim about 10 wt%.\n"
    ok, detail = pf.export_allowed("proj-b", "storm", md, settings=S())
    assert ok is False
    assert detail.get("ready") is False

    # Override all blocking
    state = pf.get_state("proj-b", "storm")
    for f in state["findings"]:
        if f["severity"] == "blocking" and f["status"] == "open":
            pf.override_finding(
                "proj-b", "storm", f["id"], actor="bob", reason="accepted"
            )
    ok2, detail2 = pf.export_allowed("proj-b", "storm", md, settings=S())
    assert ok2 is True
    assert detail2.get("ok") is True


def test_export_skipped_when_flag_off(tmp_data):
    class S:
        publication_preflight_enabled = False

    ok, detail = pf.export_allowed(
        "proj-c", "storm", "TODO still here", settings=S()
    )
    assert ok is True
    assert detail.get("skipped") is True


def test_hash_change_blocks_finalize(tmp_data):
    md1 = "Clean text with [^1].\n\n[^1]: src\n"
    pf.review_markdown("proj-d", "storm", md1, total_anchors=1)
    # Override nothing needed if clean — may have numeric? none.
    result = pf.assert_ready("proj-d", "storm", md1 + "\nextra", actor="x")
    assert result["ready"] is False
    assert any("hash" in e.lower() for e in result["errors"])


# ---- W1-10 preflight 状态机加固 ----


def test_override_preserved_after_rereview(tmp_data):
    """重新 review 同内容：已有 override 不丢失。"""
    md = "TODO fix this.\n"
    s1 = pf.review_markdown("proj-r", "storm", md)
    fid = s1["findings"][0]["id"]
    pf.override_finding("proj-r", "storm", fid, actor="alice", reason="draft ok")
    s2 = pf.review_markdown("proj-r", "storm", md)
    assert len(s2["findings"]) == 1
    f = s2["findings"][0]
    assert f["status"] == "overridden"
    assert f["resolution"]["actor"] == "alice"
    assert f["resolution"]["reason"] == "draft ok"
    assert f["stale"] is False


def test_resolve_finding_flow(tmp_data):
    """resolve 流转：open→resolved；blocking 要求 note 非空。"""
    md = "TODO fix this.\n"
    s = pf.review_markdown("proj-v", "storm", md)
    fid = s["findings"][0]["id"]
    with pytest.raises(ValueError):
        pf.resolve_finding("proj-v", "storm", fid, actor="alice", note="")
    with pytest.raises(ValueError):
        pf.resolve_finding("proj-v", "storm", fid, actor="", note="x")
    s2 = pf.resolve_finding(
        "proj-v", "storm", fid, actor="alice", note="fixed upstream"
    )
    f = next(x for x in s2["findings"] if x["id"] == fid)
    assert f["status"] == "resolved"
    assert f["resolution"]["note"] == "fixed upstream"
    assert any(e["type"] == "resolved" for e in s2["events"])
    assert s2["finalization"] is None
    with pytest.raises(ValueError):  # 已 resolved 不能再 resolve
        pf.resolve_finding("proj-v", "storm", fid, actor="alice", note="again")
    with pytest.raises(LookupError):
        pf.resolve_finding("proj-v", "storm", "no-such-id", actor="a", note="x")


def test_resolve_nonblocking_empty_note_ok(tmp_data):
    """major finding 允许空 note resolve。"""
    md = "The coating cured at 120 °C for 2 h.\n"
    s = pf.review_markdown("proj-n", "storm", md)
    fid = s["findings"][0]["id"]
    assert s["findings"][0]["severity"] == "major"
    s2 = pf.resolve_finding("proj-n", "storm", fid, actor="bob", note="")
    assert next(x for x in s2["findings"] if x["id"] == fid)["status"] == "resolved"


def test_stale_marking_on_content_change(tmp_data):
    """内容变更导致旧 finding 失配：曾被 override 的标 stale=True 并重置 open。"""
    md1 = "TODO fix this.\n"
    s1 = pf.review_markdown("proj-s", "storm", md1)
    fid = s1["findings"][0]["id"]
    pf.override_finding("proj-s", "storm", fid, actor="alice", reason="draft ok")
    md2 = "TBD fix this.\n"  # detail 从 "TODO" 变为 "TBD"，旧 key 失配
    s2 = pf.review_markdown("proj-s", "storm", md2)
    by_id = {f["id"]: f for f in s2["findings"]}
    old = by_id[fid]
    assert old["stale"] is True
    assert old["status"] == "open"
    # 新 finding 是独立的 open 项
    new = [f for f in s2["findings"] if f["id"] != fid]
    assert new
    assert all(f["status"] == "open" and f["stale"] is False for f in new)
    assert s2["finalization"] is None
    # 人工重新确认后 stale 清除
    s3 = pf.resolve_finding("proj-s", "storm", fid, actor="alice", note="re-confirmed")
    f3 = {x["id"]: x for x in s3["findings"]}[fid]
    assert f3["status"] == "resolved" and f3["stale"] is False


def test_fixed_finding_dropped_not_stale(tmp_data):
    """问题真正修复（旧 finding 失配且无人处置）→ 丢弃，不复活。"""
    md1 = "TODO fix this.\n"
    pf.review_markdown("proj-f", "storm", md1)
    md2 = "All done.\n"
    s2 = pf.review_markdown("proj-f", "storm", md2)
    assert s2["findings"] == []
    assert s2["open_blocking"] == 0


def test_stale_blocking_blocks_export(tmp_data):
    """stale + open + blocking 阻断导出，错误信息区分 stale。"""
    class S:
        publication_preflight_enabled = True

    md1 = "TODO fix this.\n"
    s1 = pf.review_markdown("proj-x", "storm", md1)
    fid = s1["findings"][0]["id"]
    pf.override_finding("proj-x", "storm", fid, actor="alice", reason="draft ok")
    md2 = "TBD fix this.\n"
    ok, detail = pf.export_allowed("proj-x", "storm", md2, settings=S())
    assert ok is False
    assert any("stale" in e for e in detail["errors"])
    # 全部重新确认后放行
    state = pf.get_state("proj-x", "storm")
    for f in state["findings"]:
        if f["severity"] == "blocking" and f["status"] == "open":
            pf.override_finding("proj-x", "storm", f["id"], actor="a", reason="ok")
    ok2, detail2 = pf.export_allowed("proj-x", "storm", md2, settings=S())
    assert ok2 is True
    assert detail2.get("ok") is True


def test_assert_ready_stale_error_message(tmp_data):
    md1 = "TODO fix this.\n"
    s1 = pf.review_markdown("proj-z", "storm", md1)
    fid = s1["findings"][0]["id"]
    pf.override_finding("proj-z", "storm", fid, actor="a", reason="ok")
    md2 = "TBD fix this.\n"
    s2 = pf.review_markdown("proj-z", "storm", md2)
    assert any(f["stale"] for f in s2["findings"])
    result = pf.assert_ready("proj-z", "storm", md2, actor="a")
    assert result["ready"] is False
    assert any("stale" in e for e in result["errors"])


def test_load_state_compat_old_json(tmp_data):
    """旧 JSON（无 stale 字段）可正常加载，stale 缺省 False。"""
    import time

    path = pf._state_path("proj-old", "storm")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "project_id": "proj-old",
        "kind": "storm",
        "content_hash": "abc",
        "findings": [
            {
                "id": "f1",
                "check": "placeholder",
                "severity": "blocking",
                "status": "overridden",
                "title": "残留占位符",
                "detail": "TODO",
                "evidence": [],
                "location": {},
                "resolution": {"kind": "overridden"},
            }
        ],
        "events": [],
        "finalization": None,
        "updated_at": time.time(),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    state = pf.get_state("proj-old", "storm")
    assert state["findings"][0]["stale"] is False
    assert state["findings"][0]["status"] == "overridden"


def test_audit_hook_maps_fail_to_major(tmp_data):
    """W1-9 钩子：flag 开启时 audit fail → major finding（check="audit"）。"""
    class S:
        preflight_artifact_audit_enabled = True

    findings = pf.run_checks(
        "Clean text.\n", settings=S(), audit_artifact_dict={"name": "x"}
    )
    audit_f = [f for f in findings if f.check == "audit"]
    assert audit_f
    assert all(f.severity == "major" and f.status == "open" for f in audit_f)

    class S2:
        preflight_artifact_audit_enabled = False

    findings2 = pf.run_checks(
        "Clean text.\n", settings=S2(), audit_artifact_dict={"name": "x"}
    )
    assert not any(f.check == "audit" for f in findings2)

    # 未传 artifact dict → 不触发审计
    findings3 = pf.run_checks("Clean text.\n", settings=S())
    assert not any(f.check == "audit" for f in findings3)

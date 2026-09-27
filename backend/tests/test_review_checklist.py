"""Tests for W3-4: review checklist (P1-32) + report appendix (P1-33)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import reviewer_fix_loop as rfl
from app.services import review_checklist as rcl
from app.services import tech_report as tr


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(rfl, "_data_root", lambda: tmp_path)
    return tmp_path


def _seed_run(session_key: str = "sess-1") -> str:
    run = rfl.new_review_run(session_key=session_key, project_id="p1")
    run = rfl.finish_review_run(run, outcome="flagged")
    rfl.save_review_run(run)
    rfl.save_dispositions(
        session_key,
        {
            "回答中数值 5.2% 缺少引用来源[^1]": {
                "status": "failure",
                "reflag_count": 2,
                "disposition": "unaddressed",
            },
            "固化剂配比 2:1 无实验依据": {
                "status": "warning",
                "reflag_count": 1,
                "disposition": "open",
            },
            "工艺流程描述完整": {
                "status": "pass",
                "reflag_count": 0,
                "disposition": "resolved",
            },
        },
    )
    return run["run_id"]


# ------------------------------------------------------------- 1. disposition→checklist 生成

def test_build_checklist_from_dispositions(tmp_data):
    run_id = _seed_run()
    checklist = rcl.build_checklist(run_id)
    assert checklist is not None
    assert checklist["run_id"] == run_id
    assert checklist["outcome"] == "flagged"
    items = {it["statement"]: it for it in checklist["items"]}
    assert len(items) == 3
    assert items["回答中数值 5.2% 缺少引用来源[^1]"]["verdict"] == "flagged"
    assert items["固化剂配比 2:1 无实验依据"]["verdict"] == "flagged"
    assert items["工艺流程描述完整"]["verdict"] == "pass"
    for it in checklist["items"]:
        assert it["id"].startswith("chk-")
        assert it["verdict"] in ("pass", "flagged", "n_a")


# ------------------------------------------------------------- 2. verdict 汇总计数

def test_verdict_summary_counts(tmp_data):
    run_id = _seed_run()
    checklist = rcl.build_checklist(run_id)
    assert checklist["summary"] == {"total": 3, "pass": 1, "flagged": 2, "n_a": 0}


# ------------------------------------------------------------- 3. evidence 引用完整

def test_evidence_refs_extracted(tmp_data):
    run_id = _seed_run()
    checklist = rcl.build_checklist(
        run_id, evidence_map={"1": {"source_id": "src-abc", "page_no": 3}}
    )
    items = {it["statement"]: it for it in checklist["items"]}
    refs = items["回答中数值 5.2% 缺少引用来源[^1]"]["evidence_refs"]
    assert refs == [{"source_id": "src-abc", "page_no": 3}]
    # 无 evidence_map 时保留原始 marker
    checklist2 = rcl.build_checklist(run_id)
    items2 = {it["statement"]: it for it in checklist2["items"]}
    assert items2["回答中数值 5.2% 缺少引用来源[^1]"]["evidence_refs"] == [
        {"source_id": "1", "page_no": None}
    ]
    # 无 marker 的条目 refs 为空
    assert items2["工艺流程描述完整"]["evidence_refs"] == []


# ------------------------------------------------------------- 4. 类别启发式

def test_category_heuristics(tmp_data):
    run_id = _seed_run()
    checklist = rcl.build_checklist(run_id)
    items = {it["statement"]: it for it in checklist["items"]}
    assert items["回答中数值 5.2% 缺少引用来源[^1]"]["category"] == "citation"
    assert items["固化剂配比 2:1 无实验依据"]["category"] == "numeric"
    assert items["工艺流程描述完整"]["category"] == "method"


def test_category_general_fallback(tmp_data):
    run = rfl.new_review_run(session_key="sess-g", project_id="p1")
    run = rfl.finish_review_run(run, outcome="pass")
    rfl.save_review_run(run)
    rfl.save_dispositions("sess-g", {"整体结论合理": {"status": "pass", "disposition": "resolved"}})
    checklist = rcl.build_checklist(run["run_id"])
    assert checklist["items"][0]["category"] == "general"


# ------------------------------------------------------------- 5. JSON 落盘（与 ReviewRun 同目录）

def test_checklist_persisted_next_to_run(tmp_data):
    run_id = _seed_run()
    rcl.build_checklist(run_id)
    safe = run_id  # run_id 仅含安全字符
    path = tmp_data / "reviews" / "runs" / f"{safe}.checklist.json"
    assert path.is_file()
    loaded = rcl.load_checklist(run_id)
    assert loaded is not None
    assert loaded["summary"]["total"] == 3
    assert rcl.load_checklist("no-such-run") is None


# ------------------------------------------------------------- 6. 缺失 run → None（fail-open）

def test_build_checklist_missing_run_returns_none(tmp_data):
    assert rcl.build_checklist("does-not-exist") is None


# ------------------------------------------------------------- 7. findings 路径

def test_build_checklist_with_findings(tmp_data):
    run_id = _seed_run()
    findings = {
        "status": "failure",
        "notes": ["回答中数值 5.2% 缺少引用来源[^1]", "新增备注：建议补充对照实验"],
        "suggestion": "请为数值补充引用并说明配比依据",
    }
    checklist = rcl.build_checklist(run_id, findings=findings)
    assert checklist is not None
    assert len(checklist["items"]) == 2
    # 第一条命中 disposition（unaddressed → flagged），第二条回退到 findings status
    assert checklist["items"][0]["verdict"] == "flagged"
    assert checklist["items"][1]["verdict"] == "flagged"
    assert all(it["reviewer_note"] == "请为数值补充引用并说明配比依据" for it in checklist["items"])


# ------------------------------------------------------------- 8. 报告附录渲染（P1-33）

def test_assemble_report_checklist_appendix(tmp_data):
    run_id = _seed_run()
    checklist = rcl.build_checklist(run_id)
    report = tr.assemble_report("doe", project_id="p-test", checklist=checklist)
    md = report["markdown"]
    assert "## 附录：Review 清单" in md
    assert "通过 1 项" in md and "标记 2 项" in md
    assert "### 标记项明细" in md
    assert "回答中数值 5.2% 缺少引用来源" in md
    # 附录位于引用清单之前
    assert md.index("附录：Review 清单") < md.index("## 引用清单")
    assert report["checklist_summary"] == {"total": 3, "pass": 1, "flagged": 2, "n_a": 0}


def test_assemble_report_without_checklist_has_no_appendix():
    report = tr.assemble_report("doe", project_id="p-test")
    assert "附录：Review 清单" not in report["markdown"]
    assert report["checklist_summary"] is None

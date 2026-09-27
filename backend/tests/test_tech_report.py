"""Unit tests for W2-9 (P1-9' tech report export + P1-34/35).

覆盖：三类报告组装、HTML 导出、pandoc 缺失回退、preflight blocking 时 409、
receipt 落盘、复现模板字段完整。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services import tech_report as tr
from app.services.wiki import report_export as rexp


# ── report_export: HTML ─────────────────────────────────────────────────────

def test_markdown_to_html_structure():
    md = "# 标题\n\n段落文本\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n- x\n- y\n"
    out = rexp.markdown_to_html(md, title="测试报告")
    s = out.decode("utf-8")
    assert s.startswith("<!DOCTYPE html>")
    assert "<title>测试报告</title>" in s
    assert "<h2>标题</h2>" in s
    assert "<table>" in s and "<th>A</th>" in s and "<td>1</td>" in s
    assert "<li>x</li>" in s
    assert "draft_not_claims" in s  # 免责声明


def test_markdown_to_html_escapes_xss():
    out = rexp.markdown_to_html("<script>alert(1)</script>", title="<b>t</b>")
    s = out.decode("utf-8")
    assert "<script>" not in s
    assert "&lt;script&gt;" in s
    assert "<b>t</b>" not in s


def test_export_capabilities_has_pandoc_and_html():
    caps = rexp.export_capabilities()
    assert caps["html"] is True
    assert caps["md"] is True
    assert "pandoc" in caps
    assert isinstance(caps["pandoc"], bool)


def test_export_bytes_html():
    payload, media_type, ext = rexp.export_bytes("# hi", "html", title="T")
    assert ext == "html"
    assert media_type == "text/html; charset=utf-8"
    assert payload.startswith(b"<!DOCTYPE html>")


# ── report_export: pandoc pipeline ──────────────────────────────────────────

def test_docx_falls_back_when_no_pandoc_no_docx(monkeypatch):
    monkeypatch.setattr(rexp.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="pandoc unavailable"):
        rexp.markdown_to_docx("# hi")


def test_pdf_falls_back_when_no_pandoc_no_fpdf(monkeypatch):
    monkeypatch.setattr(rexp.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="pandoc unavailable"):
        rexp.markdown_to_pdf("# hi")


def test_docx_uses_pandoc_when_available(monkeypatch):
    monkeypatch.setattr(rexp.shutil, "which", lambda name: "/usr/bin/pandoc")

    def fake_run(cmd, **kwargs):
        assert cmd[0] == "pandoc" and "-t" in cmd and "docx" in cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=b"FAKE-DOCX", stderr=b"")

    monkeypatch.setattr(rexp.subprocess, "run", fake_run)
    assert rexp.markdown_to_docx("# hi", title="T") == b"FAKE-DOCX"


def test_pandoc_failure_falls_back_to_native(monkeypatch):
    monkeypatch.setattr(rexp.shutil, "which", lambda name: "/usr/bin/pandoc")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"no latex")

    monkeypatch.setattr(rexp.subprocess, "run", fake_run)
    # pandoc 失败 → 回退 python-docx；本环境未装 → RuntimeError（非 pandoc 异常穿透）
    with pytest.raises(RuntimeError):
        rexp.markdown_to_docx("# hi")


# ── tech_report: assemble ───────────────────────────────────────────────────

def test_assemble_unknown_kind_raises():
    with pytest.raises(ValueError, match="unknown report kind"):
        tr.assemble_report("paper", project_id="p1")


def test_strip_academic_fields():
    obj = {
        "title": "环氧配方",
        "zotero": {"key": "ABC"},
        "meta": {"bibtex": "@article{}", "csl": "apa", "doi": "10.1/x"},
    }
    out = tr._strip_academic_fields(obj)
    assert out["title"] == "环氧配方"
    assert "zotero" not in out
    assert "bibtex" not in out["meta"] and "csl" not in out["meta"]
    assert out["meta"]["doi"] == "10.1/x"  # 非学术字段保留


def test_assemble_formulation_fail_open(monkeypatch):
    # 数据源抛错 → fail-open，missing 标注而非抛错
    def _boom(pid):
        raise RuntimeError("db down")

    monkeypatch.setitem(tr._GATHER, "formulation", _boom)
    report = tr.assemble_report("formulation", project_id="p1")
    assert report["kind"] == "formulation"
    assert report["missing"]
    assert "配方技术报告" in report["markdown"]


def test_assemble_doe_no_data(monkeypatch, tmp_path):
    monkeypatch.setitem(
        tr._GATHER, "doe", lambda pid: ("", ["暂无 DOE 方案记录"])
    )
    report = tr.assemble_report("doe", project_id="p1")
    assert "暂无可用数据" in report["markdown"] or "暂无 DOE" in report["markdown"]
    assert "引用清单" in report["markdown"]


def test_gather_optimization_from_task_snapshot(tmp_path, monkeypatch):
    snap_dir = tmp_path / "tasks"
    snap_dir.mkdir()
    snap = {
        "task_id": "t1",
        "kind": "optimize",
        "state": "completed",
        "result": {"engine": "baybe", "iterations": 20, "best_value": 0.93,
                   "best_params": {"树脂": 45.0}},
    }
    (snap_dir / "t1.json").write_text(json.dumps(snap), encoding="utf-8")
    monkeypatch.setattr(tr, "_task_persist_dir", lambda: snap_dir)
    body, missing = tr._gather_optimization("p1")
    assert "baybe" in body
    assert "0.93" in body
    assert "树脂" in body
    assert missing == []


def test_gather_optimization_no_snapshot_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "_task_persist_dir", lambda: tmp_path / "nonexistent")
    body, missing = tr._gather_optimization("p1")
    assert body == ""
    assert missing


# ── tech_report: P1-34 receipt ──────────────────────────────────────────────

def test_write_output_receipt(tmp_path):
    path = tr.write_output_receipt(
        "optimize", "task-1",
        inputs={"requirement": "高耐盐雾", "iterations": 20},
        outputs={"best_value": 0.93, "candidates": [{"a": 1}]},
        data_dir=tmp_path,
    )
    assert path is not None and path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["run_kind"] == "optimize"
    assert data["run_id"] == "task-1"
    assert len(data["input_hash"]) == 64
    assert "timestamp" in data
    keys = {m["key"] for m in data["outputs_manifest"]}
    assert {"best_value", "candidates"} <= keys


def test_write_output_receipt_fail_open():
    # 不可写目录 → 返回 None，不抛错
    assert tr.write_output_receipt("x", "y", data_dir="/proc/definitely-not-here") is None


# ── tech_report: P1-35 repro template ───────────────────────────────────────

def test_render_repro_template_fields():
    md = tr.render_repro_template(
        run_kind="optimize",
        run_id="t1",
        params={"iterations": 20},
        input_hash="abc123",
        outputs_manifest=[{"key": "best_value", "type": "float", "size": 4}],
        artifact={"name": "r", "version": "1.0", "params": {"seed": 1}},
    )
    for section in ("## 环境", "## 参数", "## 输入校验", "## 输出校验", "## 产物审计"):
        assert section in md
    assert "abc123" in md
    assert "iterations" in md


def test_render_repro_template_without_artifact():
    md = tr.render_repro_template(run_kind="doe_cycle", run_id="t2")
    assert "## 环境" in md
    assert "未提供 artifact" in md


# ── API: preflight gate ─────────────────────────────────────────────────────

def _import_api():
    from app.api import tech_reports as api

    return api


def test_export_blocked_returns_409(monkeypatch):
    api = _import_api()
    monkeypatch.setattr(
        "app.services.tech_report.assemble_report",
        lambda kind, project_id: {"markdown": "# 报告 [^1]\n\n[^1]: x"},
    )
    monkeypatch.setattr("app.config.get_settings", lambda: object())
    monkeypatch.setattr(
        "app.services.publication_preflight.export_allowed",
        lambda pid, kind, md, settings: (False, {"errors": ["1 open blocking"]}),
    )
    req = api.TechReportExportRequest(kind="formulation", format="html", project_id="p1")
    with pytest.raises(HTTPException) as ei:
        api.export_tech_report(req)
    assert ei.value.status_code == 409
    assert ei.value.detail["error"] == "publication_preflight_blocked"


def test_export_html_success(monkeypatch):
    api = _import_api()
    monkeypatch.setattr(
        "app.services.tech_report.assemble_report",
        lambda kind, project_id: {"markdown": "# 报告\n\n| A | B |\n|---|---|\n| 1 | 2 |"},
    )
    monkeypatch.setattr("app.config.get_settings", lambda: object())
    monkeypatch.setattr(
        "app.services.publication_preflight.export_allowed",
        lambda pid, kind, md, settings: (True, {"ok": True}),
    )
    req = api.TechReportExportRequest(kind="doe", format="html", project_id="p1")
    resp = api.export_tech_report(req)
    assert resp.media_type == "text/html; charset=utf-8"
    assert b"<table>" in resp.body
    assert "doe_report_p1.html" in resp.headers["Content-Disposition"]


def test_export_docx_no_backend_returns_503(monkeypatch):
    api = _import_api()
    monkeypatch.setattr(
        "app.services.tech_report.assemble_report",
        lambda kind, project_id: {"markdown": "# 报告"},
    )
    monkeypatch.setattr("app.config.get_settings", lambda: object())
    monkeypatch.setattr(
        "app.services.publication_preflight.export_allowed",
        lambda pid, kind, md, settings: (True, {"ok": True}),
    )
    monkeypatch.setattr(rexp.shutil, "which", lambda name: None)  # 无 pandoc/docx
    req = api.TechReportExportRequest(kind="optimization", format="docx", project_id="p1")
    with pytest.raises(HTTPException) as ei:
        api.export_tech_report(req)
    assert ei.value.status_code == 503

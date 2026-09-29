"""Technical report assembly (P1-9') + run output receipts (P1-34) + repro template (P1-35).

Assembles formulation / DOE / optimization reports as Markdown (de-academized:
no Zotero/BibTeX/CSL fields), converts via ``services.wiki.report_export``.
All data gathering is best-effort / fail-open: missing sources are listed in
the report's ``missing`` section instead of raising.

Module-level imports are stdlib-only so ``worker/tasks.py`` can import
:func:`write_output_receipt` without pulling DB/session machinery.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPORT_KINDS = ("formulation", "doe", "optimization")

# Academic-only metadata keys stripped from tech reports (P1-9' de-academized).
_ACADEMIC_KEY_RE = re.compile(r"zotero|bibtex|csl|ris\b|citation[-_]?key", re.IGNORECASE)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _data_root() -> Path:
    return Path(os.environ.get("FORMUMIND_DATA_DIR", "./data")).resolve()


def _strip_academic_fields(obj: Any) -> Any:
    """Recursively drop Zotero/BibTeX/CSL-style metadata keys."""
    if isinstance(obj, dict):
        return {
            k: _strip_academic_fields(v)
            for k, v in obj.items()
            if not _ACADEMIC_KEY_RE.search(str(k))
        }
    if isinstance(obj, list):
        return [_strip_academic_fields(v) for v in obj]
    return obj


def _md_table(headers: list[str], rows: list[list[Any]], *, max_rows: int = 50) -> str:
    if not headers:
        return ""
    lines = [
        "| " + " | ".join(str(h) for h in headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for row in rows[:max_rows]:
        cells = [str(c)[:120] if c is not None else "" for c in (list(row) + [""] * len(headers))[: len(headers)]]
        lines.append("| " + " | ".join(cells) + " |")
    if len(rows) > max_rows:
        lines.append(f"\n> …共 {len(rows)} 行，仅展示前 {max_rows} 行")
    return "\n".join(lines)


def _safe_str(value: Any, limit: int = 200) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(_strip_academic_fields(value), ensure_ascii=False)[:limit]
        except Exception:
            return str(value)[:limit]
    return str(value)[:limit]


# ── Data gatherers (lazy imports, each fail-open) ────────────────────────────

def _gather_formulation(project_id: str) -> tuple[str, list[str]]:
    """Latest formulation snapshot per lineage → ingredient tables."""
    try:
        from .formulation_history import get_history_store
    except Exception as exc:
        return "", [f"配方数据源不可用：{exc}"]
    try:
        store = get_history_store()
        lineage_ids = store.find_lineages(project_id=project_id, limit=5)
    except Exception as exc:
        logger.warning("formulation lineage lookup failed: %s", exc)
        return "", [f"配方版本查询失败：{exc}"]
    if not lineage_ids:
        return "", ["该项目暂无配方版本记录"]
    parts = ["## 配方报告\n"]
    missing: list[str] = []
    for lid in lineage_ids:
        try:
            ver = store.latest(lid)
        except Exception as exc:
            missing.append(f"配方 lineage {lid[:8]} 读取失败：{exc}")
            continue
        if ver is None:
            continue
        snap = _strip_academic_fields(getattr(ver, "snapshot", None) or {})
        ingredients = snap.get("ingredients") or []
        parts.append(f"### {ver.name}（v{ver.version}）\n")
        meta = [
            ("领域", getattr(ver, "domain", "")),
            ("变更说明", getattr(ver, "change_summary", "") or "—"),
            ("创建者", getattr(ver, "created_by", "") or "—"),
        ]
        parts.append(_md_table(["属性", "值"], meta) + "\n")
        if ingredients:
            rows = [
                [
                    ing.get("name", ""),
                    ing.get("percentage", ing.get("amount", "")),
                    ing.get("function", ing.get("role", "")),
                ]
                for ing in ingredients
                if isinstance(ing, dict)
            ]
            parts.append("**配方组成**\n")
            parts.append(_md_table(["组分", "配比", "作用"], rows) + "\n")
        else:
            missing.append(f"配方 {ver.name} v{ver.version} 无组分明细")
    return "\n".join(parts), missing


def _gather_doe(project_id: str) -> tuple[str, list[str]]:
    """Recent DOE plans → factor/run tables."""
    try:
        from ..db import doe_plan_store
        from ..db.database import default_session_factory
    except Exception as exc:
        return "", [f"DOE 数据源不可用：{exc}"]
    try:
        factory = default_session_factory()
        with factory() as session:
            items, total = doe_plan_store.list_history(
                session, project_id=project_id or None, page=1, page_size=3
            )
    except Exception as exc:
        logger.warning("doe history lookup failed: %s", exc)
        return "", [f"DOE 历史查询失败：{exc}"]
    if not items:
        return "", ["暂无 DOE 方案记录"]
    parts = [f"## DOE 报告（共 {total} 个方案，展示最新 {len(items)} 个）\n"]
    missing: list[str] = []
    for item in items:
        item = _strip_academic_fields(item if isinstance(item, dict) else {})
        parts.append(f"### DOE 方案：{item.get('design', '?')}（{item.get('plan_id', '')[:8]}）\n")
        factors = item.get("factors") or []
        if factors:
            rows = [
                [f.get("name", ""), f.get("low", ""), f.get("high", ""), f.get("unit", "")]
                for f in factors
                if isinstance(f, dict)
            ]
            parts.append("**试验因素**\n")
            parts.append(_md_table(["因素", "低水平", "高水平", "单位"], rows) + "\n")
        runs = item.get("runs") or item.get("matrix") or []
        if runs:
            first = runs[0] if isinstance(runs[0], dict) else {}
            headers = list(first.keys())[:8]
            parts.append("**试验矩阵（前 8 列）**\n")
            parts.append(
                _md_table(headers, [[r.get(h, "") for h in headers] for r in runs if isinstance(r, dict)]) + "\n"
            )
        else:
            missing.append(f"DOE 方案 {str(item.get('plan_id', ''))[:8]} 无试验矩阵")
    return "\n".join(parts), missing


def _task_persist_dir() -> Path:
    return Path(os.environ.get("FORMUMIND_TASK_DIR", "/tmp/formumind_tasks"))


def _gather_optimization(project_id: str) -> tuple[str, list[str]]:
    """Latest completed optimize task snapshot → result summary."""
    d = _task_persist_dir()
    if not d.is_dir():
        return "", ["暂无优化任务记录（任务快照目录不存在）"]
    best: dict[str, Any] | None = None
    best_mtime = -1.0
    try:
        for p in d.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("kind") != "optimize" or data.get("state") != "completed":
                continue
            mtime = p.stat().st_mtime
            if mtime > best_mtime:
                best_mtime = mtime
                best = data
    except Exception as exc:
        return "", [f"优化任务快照扫描失败：{exc}"]
    if best is None:
        return "", ["暂无已完成的优化任务"]
    result = _strip_academic_fields(best.get("result") or {})
    parts = ["## 优化报告\n"]
    parts.append(f"任务 ID：`{best.get('task_id', '')}`\n")
    summary_rows = [
        ("引擎", _safe_str(result.get("engine", result.get("optimizer", "")))),
        ("迭代次数", _safe_str(result.get("iterations", result.get("n_iter", "")))),
        ("最优目标值", _safe_str(result.get("best_value", result.get("best_score", "")))),
        ("收敛", _safe_str(result.get("converged", ""))),
    ]
    parts.append("**优化结果摘要**\n")
    parts.append(_md_table(["指标", "值"], summary_rows) + "\n")
    best_params = result.get("best_params") or result.get("best_point") or {}
    if isinstance(best_params, dict) and best_params:
        parts.append("**最优参数**\n")
        parts.append(_md_table(["参数", "取值"], [[k, _safe_str(v)] for k, v in best_params.items()]) + "\n")
    return "\n".join(parts), []


_KIND_TITLE = {
    "formulation": "配方技术报告",
    "doe": "DOE 技术报告",
    "optimization": "优化技术报告",
}
_GATHER = {
    "formulation": _gather_formulation,
    "doe": _gather_doe,
    "optimization": _gather_optimization,
}


def assemble_report(
    kind: str,
    *,
    project_id: str,
    checklist: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble a de-academized technical report as Markdown (fail-open).

    Returns ``{"kind","project_id","title","markdown","missing","generated_at",
    "checklist_summary"}``. Unknown kind → ValueError. Missing data → noted
    in ``missing``, never raised. ``checklist`` (optional, P1-33) is a
    ``review_checklist.build_checklist`` payload rendered as an appendix.
    """
    if kind not in _GATHER:
        raise ValueError(f"unknown report kind: {kind!r} (expected one of {REPORT_KINDS})")
    title = _KIND_TITLE[kind]
    missing: list[str] = []
    try:
        body, miss = _GATHER[kind](project_id or "")
        missing.extend(miss)
    except Exception as exc:  # noqa: BLE001 — fail-open by contract
        logger.warning("assemble_report(%s) failed: %s", kind, exc)
        body, missing = "", [f"报告组装异常：{exc}"]
    header = (
        f"# {title}\n\n"
        f"- 项目：`{project_id}`\n"
        f"- 生成时间：{_utcnow_iso()}\n"
        f"- 类型：技术报告（去学术化，不含 Zotero/BibTeX/CSL 字段）\n\n"
    )
    missing_block = ""
    if missing:
        missing_block = "## 数据缺失说明\n\n" + "\n".join(f"- {m}" for m in missing) + "\n\n"
    checklist_block = ""
    checklist_summary: dict[str, Any] | None = None
    if checklist:
        try:
            from .review_checklist import render_checklist_markdown

            rendered = render_checklist_markdown(checklist)
            if rendered:
                checklist_block = rendered + "\n\n"
            checklist_summary = checklist.get("summary")
        except Exception as exc:  # noqa: BLE001 — appendix must never break assembly
            logger.warning("checklist appendix skipped: %s", exc)
    refs = "## 引用清单\n\n> 本报告由系统数据确定性组装；引用请回链 source_ids / 测量行。\n"
    return {
        "kind": kind,
        "project_id": project_id,
        "title": title,
        "markdown": header + (body or "> 暂无可用数据\n\n") + missing_block + checklist_block + refs,
        "missing": missing,
        "generated_at": _utcnow_iso(),
        "checklist_summary": checklist_summary,
    }


# ── P1-34: output receipts ───────────────────────────────────────────────────

def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except Exception:
        return _safe_str(value, 500)


def _manifest_outputs(outputs: Any) -> list[dict[str, Any]]:
    """Summarize outputs as a manifest (keys + types + sizes, not full blobs)."""
    manifest: list[dict[str, Any]] = []
    if isinstance(outputs, dict):
        items = outputs.items()
    elif isinstance(outputs, list):
        items = enumerate(outputs)
    else:
        return [{"type": type(outputs).__name__, "size": len(str(outputs))}]
    for k, v in items:
        entry: dict[str, Any] = {"key": str(k), "type": type(v).__name__}
        try:
            entry["size"] = len(v) if hasattr(v, "__len__") else len(str(v))
        except Exception:
            entry["size"] = None
        if isinstance(v, (str, int, float, bool)) or v is None:
            entry["preview"] = str(v)[:120]
        manifest.append(entry)
    return manifest


def write_output_receipt(
    run_kind: str,
    run_id: str,
    *,
    inputs: Any = None,
    outputs: Any = None,
    data_dir: Path | str | None = None,
) -> Path | None:
    """Persist ``output-receipt.json`` for a finished run (P1-34). Fail-open.

    Records input hash, output manifest (keys/types/sizes) and timestamp.
    Returns the receipt path, or None when persistence failed.
    """
    try:
        root = Path(data_dir) if data_dir else _data_root()
        safe_kind = re.sub(r"[^\w.\-]+", "_", str(run_kind or "run"))[:60] or "run"
        safe_id = re.sub(r"[^\w.\-]+", "_", str(run_id or "unknown"))[:120] or "unknown"
        target = root / "output_receipts" / safe_kind / f"{safe_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        inputs_json = _jsonable(inputs)
        input_hash = hashlib.sha256(
            json.dumps(inputs_json, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        receipt = {
            "run_kind": safe_kind,
            "run_id": safe_id,
            "input_hash": input_hash,
            "inputs_summary": list(inputs_json.keys()) if isinstance(inputs_json, dict) else type(inputs_json).__name__,
            "outputs_manifest": _manifest_outputs(outputs),
            "timestamp": _utcnow_iso(),
        }
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
        return target
    except Exception as exc:  # noqa: BLE001 — receipts must never break a run
        logger.warning("write_output_receipt(%s/%s) failed: %s", run_kind, run_id, exc)
        return None


# ── P1-35: reproduction report template ──────────────────────────────────────

def _env_snapshot() -> dict[str, str]:
    snap = {
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    for pkg in ("fastapi", "pydantic", "sqlalchemy", "numpy", "pandas"):
        try:
            mod = __import__(pkg)
            snap[pkg] = getattr(mod, "__version__", "?")
        except Exception:
            snap[pkg] = "not installed"
    return snap


def render_repro_template(
    *,
    run_kind: str,
    run_id: str,
    params: dict[str, Any] | None = None,
    input_hash: str | None = None,
    outputs_manifest: list[dict[str, Any]] | None = None,
    artifact: dict[str, Any] | None = None,
) -> str:
    """Render a reproduction-report Markdown template (P1-35).

    Reuses :func:`artifact_audit.audit_artifact` for the artifact checklist
    when ``artifact`` is provided.
    """
    lines = [
        "# 复现报告（模板）",
        "",
        f"- 运行类型：`{run_kind}`",
        f"- 运行 ID：`{run_id}`",
        f"- 生成时间：{_utcnow_iso()}",
        "",
        "## 环境",
        "",
        _md_table(["组件", "版本"], [[k, v] for k, v in _env_snapshot().items()]),
        "",
        "## 参数",
        "",
    ]
    if params:
        lines.append(_md_table(["参数", "取值"], [[k, _safe_str(v)] for k, v in params.items()]))
    else:
        lines.append("> （未提供参数）")
    lines += [
        "",
        "## 输入校验",
        "",
        f"- 输入 hash：`{input_hash or '（未提供）'}`",
        "",
        "## 输出校验",
        "",
    ]
    if outputs_manifest:
        lines.append(
            _md_table(
                ["输出项", "类型", "大小"],
                [[m.get("key", ""), m.get("type", ""), m.get("size", "")] for m in outputs_manifest],
            )
        )
    else:
        lines.append("> （未提供输出清单）")
    lines += ["", "## 产物审计（artifact_audit）", ""]
    if artifact is not None:
        try:
            from .artifact_audit import audit_artifact

            findings = audit_artifact(artifact)
            rows = []
            for f in findings:
                d = f if isinstance(f, dict) else getattr(f, "__dict__", {})
                rows.append([d.get("check", ""), d.get("status", d.get("ok", "")), _safe_str(d.get("detail", ""), 160)])
            lines.append(_md_table(["检查项", "状态", "说明"], rows) if rows else "> 审计无输出")
        except Exception as exc:
            lines.append(f"> 产物审计执行失败：{exc}")
    else:
        lines.append("> （未提供 artifact 字典）")
    lines += ["", "> 本报告为模板：填入实际值后可作为复现依据。", ""]
    return "\n".join(lines)

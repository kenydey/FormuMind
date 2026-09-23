"""Phase 3: stitch sections, strip unbound citations, append Pack appendix."""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from .storm_schema import ReportOutline, SectionDraft

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str, str, float, dict | None], None]

_CITE_RE = re.compile(r"\[\^(\d+)\]")


def _strip_unbound_citations(text: str, max_index: int) -> str:
    def repl(m: re.Match[str]) -> str:
        try:
            n = int(m.group(1))
        except ValueError:
            return ""
        return m.group(0) if 1 <= n <= max_index else ""

    return _CITE_RE.sub(repl, text or "")


def _pack_appendix(pack: dict[str, Any]) -> str:
    lines = [
        "## 附录：卷宗确定性切片（L1）",
        "",
        "> 下列表格来自 DossierPack，数值以系统为准；正文不得覆盖。",
        "",
        "### 技术要求",
        "",
        "| 指标 | 值 | 单位 | 方向 |",
        "| --- | --- | --- | --- |",
    ]
    for r in ((pack.get("requirements") or {}).get("rows") or [])[:12]:
        if isinstance(r, dict):
            lines.append(
                f"| {r.get('metric')} | {r.get('value')} | {r.get('unit') or ''} | "
                f"{r.get('direction') or ''} |"
            )
    lines += ["", "### 基准配方", "", "| 组分 | 角色 | Wt% | CAS |", "| --- | --- | --- | --- |"]
    for r in ((pack.get("formula") or {}).get("rows") or [])[:12]:
        if isinstance(r, dict):
            lines.append(
                f"| {r.get('name')} | {r.get('role')} | {r.get('weight_pct')} | "
                f"{r.get('cas') or ''} |"
            )
    source_ids = (pack.get("literature") or {}).get("source_ids") or []
    lines += ["", "### 文献 source_ids", ""]
    if source_ids:
        for sid in source_ids[:20]:
            lines.append(f"- `{sid}`")
    else:
        lines.append("- （无）")
    lines += [
        "",
        "---",
        "",
        "**Disclaimer:** `draft_not_claims` — 长文为研发草稿，不进 Claims / DOE bounds。",
        "",
    ]
    return "\n".join(lines)


def stitch_and_polish(
    outline: ReportOutline,
    drafts: dict[str, SectionDraft],
    pack: dict[str, Any],
    *,
    progress_cb: ProgressCb | None = None,
) -> tuple[str, list[str]]:
    """Concatenate drafts, scrub unbound [^n], append pack appendix.

    Returns (markdown, all_citation_ids).
    """
    if progress_cb:
        progress_cb("stitching", "正在缝合章节…", 0.82, None)

    cite_pool: list[str] = []
    for spec in outline.sections:
        d = drafts.get(spec.section_id)
        if d:
            for c in d.used_citations:
                if c and c not in cite_pool:
                    cite_pool.append(c)

    parts: list[str] = [
        f"# {outline.topic}",
        "",
        f"> 项目：`{outline.project_id}` · STORM 长文草稿 · **draft_not_claims**",
        "",
        outline.global_summary_goal or "",
        "",
        f"视角：{' · '.join(outline.perspectives) or '—'}",
        "",
    ]

    if progress_cb:
        progress_cb("linting", "引用校验与附录…", 0.9, None)

    for spec in outline.sections:
        d = drafts.get(spec.section_id)
        if not d:
            parts.append(f"## {spec.title}\n\n_（本章未生成）_\n")
            continue
        body = _strip_unbound_citations(d.content_markdown, max_index=max(len(cite_pool), 1))
        # Ensure heading once
        if not body.lstrip().startswith("#"):
            body = f"## {spec.title}\n\n{body}"
        parts.append(body.rstrip())
        parts.append("")

    if cite_pool:
        parts.append("## 引用清单")
        parts.append("")
        for i, cid in enumerate(cite_pool, 1):
            parts.append(f"[^{i}]: `{cid}`")
        parts.append("")

    parts.append(_pack_appendix(pack))
    return "\n".join(parts).strip() + "\n", cite_pool

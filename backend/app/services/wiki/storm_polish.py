"""Phase 3: stitch sections, citation_binder scrub, soft-rules + Pack appendix."""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from ...domain.citations import CitationAnchor
from .storm_schema import ReportOutline, SectionDraft

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str, str, float, dict | None], None]

_CITE_RE = re.compile(r"\[\^(\d+)\]")


def _parse_page(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _source_id_from_ident(ident: str) -> str:
    """kb:src#c0 → src; plain ids unchanged."""
    s = (ident or "").strip()
    if s.startswith("kb:") and "#" in s:
        return s[3:].split("#", 1)[0]
    if s.startswith("kb:"):
        return s[3:]
    return s


def evidence_to_anchors(evidence: list[dict[str, str]]) -> list[CitationAnchor]:
    """Map section retrieval hits → CitationAnchor (index 0 → [^1])."""
    anchors: list[CitationAnchor] = []
    for i, e in enumerate(evidence or []):
        sid = str(e.get("id") or e.get("title") or f"ev-{i + 1}").strip()
        if not sid:
            continue
        text = str(e.get("snippet") or e.get("title") or sid)[:200]
        page = _parse_page(e.get("page"))
        anchors.append(
            CitationAnchor(
                chunk_id=sid,
                source_id=_source_id_from_ident(sid),
                text=text,
                page=page,
            )
        )
    return anchors


def _page_for_source(source_id: str) -> int | None:
    """Best-effort first chunk page_no for a source_id."""
    try:
        from ...db.chunk_store import get_chunk_store

        sid = _source_id_from_ident(source_id)
        rows = get_chunk_store().get_by_source(sid)
        for row in rows or []:
            page = getattr(row, "page_no", None)
            if page is not None:
                return int(page)
    except Exception as exc:
        logger.debug("chunk page lookup failed for %s: %s", source_id, exc)
    return None


def _anchors_from_source_ids(
    source_ids: list[str],
    pack: dict[str, Any],
) -> list[CitationAnchor]:
    """Build anchors for the global citation pool (polish footnotes).

    Wave A: attach page_no from document_chunks when available so footnotes
    render as ``[^n]: Source: id, pp. N`` (aligned with chat context).
    """
    lit_rows = (pack.get("literature") or {}).get("rows") or []
    title_by_id: dict[str, str] = {}
    snippet_by_id: dict[str, str] = {}
    page_by_id: dict[str, int] = {}
    for r in lit_rows:
        if isinstance(r, dict) and r.get("source_id"):
            sid = str(r["source_id"])
            title_by_id[sid] = str(r.get("title") or sid)
            snippet_by_id[sid] = str(r.get("snippet") or "")[:200]
            p = _parse_page(r.get("page") or r.get("page_no"))
            if p is not None:
                page_by_id[sid] = p
    anchors: list[CitationAnchor] = []
    for sid in source_ids:
        preview = snippet_by_id.get(sid) or title_by_id.get(sid) or sid
        page = page_by_id.get(sid)
        if page is None:
            page = _page_for_source(sid)
        anchors.append(
            CitationAnchor(
                chunk_id=sid,
                source_id=_source_id_from_ident(sid),
                text=preview[:200],
                page=page,
            )
        )
    return anchors


def remap_local_citations_to_global(
    text: str,
    local_ids: list[str],
    global_pool: list[str],
) -> str:
    """Rewrite section-local [^n] (bound to local_ids) → global pool indices.

    Unbound / unknown markers are stripped (empty string).
    """
    id_to_global: dict[str, int] = {
        sid: i + 1 for i, sid in enumerate(global_pool) if sid
    }

    def repl(m: re.Match[str]) -> str:
        try:
            n = int(m.group(1))
        except ValueError:
            return ""
        if not (1 <= n <= len(local_ids)):
            return ""
        sid = local_ids[n - 1]
        g = id_to_global.get(sid)
        return f"[^{g}]" if g is not None else ""

    return _CITE_RE.sub(repl, text or "")


def scrub_unbound_citations(text: str, total_anchors: int) -> tuple[str, list[int]]:
    """Strip hallucinated [^n] outside [1, total_anchors]; return (text, dropped)."""
    from ...services.citation_binder import (
        extract_citation_indices_validated,
        strip_existing_footnotes,
    )

    clean = strip_existing_footnotes(text or "")
    _valid, out_of_range = extract_citation_indices_validated(clean, total_anchors)

    def repl(m: re.Match[str]) -> str:
        try:
            n = int(m.group(1))
        except ValueError:
            return ""
        if total_anchors <= 0:
            return ""
        return m.group(0) if 1 <= n <= total_anchors else ""

    scrubbed = _CITE_RE.sub(repl, clean)
    return scrubbed, list(out_of_range)


def _formulation_from_pack(pack: dict[str, Any]):
    """Best-effort Formulation for soft rules; None if pack has no usable rows."""
    from ...domain.schemas import Formulation, Ingredient, ProductDomain

    rows = (pack.get("formula") or {}).get("rows") or []
    ingredients: list[Ingredient] = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("name"):
            continue
        try:
            wt = float(r.get("weight_pct") or 0)
        except (TypeError, ValueError):
            wt = 0.0
        wt = max(0.0, min(wt, 100.0))
        ingredients.append(
            Ingredient(
                name=str(r["name"]),
                role=str(r.get("role") or "additive"),
                weight_pct=wt,
                cas_no=(str(r["cas"]).strip() if r.get("cas") else None),
            )
        )
    if not ingredients:
        return None

    domain_raw = str(pack.get("domain") or "anticorrosion_coating")
    try:
        domain = ProductDomain(domain_raw)
    except ValueError:
        domain = ProductDomain.anticorrosion_coating
    return Formulation(
        name=str(pack.get("title") or "dossier-baseline")[:120],
        domain=domain,
        ingredients=ingredients,
        source="dossier_pack",
    )


def collect_soft_rule_warnings(pack: dict[str, Any]) -> list[str]:
    """Read-only feasibility / acid_stability warnings — never mutates formulas."""
    warnings: list[str] = []
    form = _formulation_from_pack(pack)
    if form is None:
        return ["（卷宗无基准配方行，跳过规则审查。）"]

    try:
        from ...services.feasibility import check_formulation

        verdict = check_formulation(form, req=None)
        if verdict.reasons:
            for reason in verdict.reasons[:12]:
                warnings.append(f"[feasibility:{verdict.status}] {reason}")
        elif not verdict.feasible:
            warnings.append(f"[feasibility:{verdict.status}] 配方未通过可行性门（无明细）。")
        else:
            warnings.append(f"[feasibility:{verdict.status}] 未发现拦截项。")
    except Exception as exc:
        logger.debug("storm soft feasibility soft-failed: %s", exc)
        warnings.append(f"[feasibility:unavailable] {exc}")

    try:
        from ...services.acid_stability import check_acid_stability

        acid = check_acid_stability(form)
        if acid.reasons:
            for reason in acid.reasons[:8]:
                warnings.append(f"[acid_stability:{acid.status}] {reason}")
        else:
            warnings.append(f"[acid_stability:{acid.status}] 酸稳定性筛查通过。")
    except Exception as exc:
        logger.debug("storm soft acid_stability soft-failed: %s", exc)
        warnings.append(f"[acid_stability:unavailable] {exc}")

    return warnings


def _rules_appendix(warnings: list[str]) -> str:
    lines = [
        "## 附录：规则审查（只读）",
        "",
        "> Soft audit：可行性门 / 酸稳定性。仅警告列表，**不**自动改配方、不进 Claims/DOE。",
        "",
    ]
    if not warnings:
        lines.append("- （无警告）")
    else:
        for w in warnings:
            lines.append(f"- {w}")
    lines.append("")
    return "\n".join(lines)


_NUMERIC_TOKEN = re.compile(r"(?<![A-Za-z])(\d+(?:\.\d+)?)(?![A-Za-z])")


def collect_numeric_fidelity_warnings(markdown: str, pack: dict[str, Any]) -> list[str]:
    """Wave C: flag body numbers that disagree with pack requirement/formula tables.

    Soft only — appends warnings; never rewrites Claims / DOE bounds.
    """
    warnings: list[str] = []
    pack_vals: list[tuple[str, float]] = []
    for r in ((pack.get("requirements") or {}).get("rows") or []):
        if not isinstance(r, dict):
            continue
        raw = r.get("value")
        try:
            pack_vals.append((str(r.get("metric") or "metric"), float(raw)))
        except (TypeError, ValueError):
            continue
    for r in ((pack.get("formula") or {}).get("rows") or []):
        if not isinstance(r, dict):
            continue
        raw = r.get("weight_pct")
        try:
            pack_vals.append((str(r.get("name") or "wt"), float(raw)))
        except (TypeError, ValueError):
            continue
    if not pack_vals:
        return warnings
    # Strip appendix tables from comparison (body only).
    body = (markdown or "").split("## 附录")[0]
    body_nums = []
    for m in _NUMERIC_TOKEN.finditer(body):
        try:
            body_nums.append(float(m.group(1)))
        except ValueError:
            continue
    if not body_nums:
        return warnings
    for label, expected in pack_vals[:20]:
        if expected <= 0:
            continue
        # Prefer exact or near match (±5%); else if a close but wrong neighbor exists, warn.
        if any(abs(n - expected) / max(abs(expected), 1e-9) <= 0.05 for n in body_nums):
            continue
        near = [
            n
            for n in body_nums
            if 0.05 < abs(n - expected) / max(abs(expected), 1e-9) <= 0.25
        ]
        if near:
            warnings.append(
                f"数值存疑：正文出现接近但非卷宗值的数字（{label} 卷宗={expected:g}，正文≈{near[0]:g}）"
            )
    return warnings[:12]


def _numeric_appendix(warnings: list[str]) -> str:
    lines = [
        "## 附录：数值保真检查（只读）",
        "",
        "> 将正文数字与卷宗 L1 表交叉比对；不一致仅标记，不自动改写。",
        "",
    ]
    if not warnings:
        lines.append("- （无数值存疑）")
    else:
        for w in warnings:
            lines.append(f"- {w}")
    lines.append("")
    return "\n".join(lines)


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
    """Concatenate drafts, remap+scrub citations via citation_binder, append appendices.

    Returns (markdown, global_citation_source_ids).
    """
    from ...services.citation_binder import build_footnotes_section

    if progress_cb:
        progress_cb("stitching", "正在缝合章节…", 0.82, None)

    cite_pool: list[str] = []
    for spec in outline.sections:
        d = drafts.get(spec.section_id)
        if d:
            for c in d.used_citations:
                if c and c not in cite_pool:
                    cite_pool.append(c)

    anchors = _anchors_from_source_ids(cite_pool, pack)
    n_anchors = len(anchors)

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
        progress_cb("linting", "引用校验与规则附录…", 0.9, None)

    dropped_total: list[int] = []
    used_global: set[int] = set()

    for spec in outline.sections:
        d = drafts.get(spec.section_id)
        if not d:
            parts.append(f"## {spec.title}\n\n_（本章未生成）_\n")
            continue
        local_ids = list(d.used_citations or [])
        body = remap_local_citations_to_global(
            d.content_markdown, local_ids, cite_pool
        )
        body, dropped = scrub_unbound_citations(body, n_anchors)
        dropped_total.extend(dropped)
        for m in _CITE_RE.finditer(body):
            try:
                used_global.add(int(m.group(1)))
            except ValueError:
                pass
        if not body.lstrip().startswith("#"):
            body = f"## {spec.title}\n\n{body}"
        parts.append(body.rstrip())
        parts.append("")

    if anchors and used_global:
        footnotes = build_footnotes_section(anchors, used_indices=sorted(used_global))
        parts.append("## 引用清单")
        parts.append(footnotes)
        parts.append("")
    elif cite_pool:
        parts.append("## 引用清单")
        parts.append("")
        for i, cid in enumerate(cite_pool, 1):
            parts.append(f"[^{i}]: `{cid}`")
        parts.append("")

    if dropped_total:
        parts.append(
            f"> 已剔除未绑定引用标记：{', '.join(f'[^{n}]' for n in sorted(set(dropped_total)))}"
        )
        parts.append("")

    soft_warnings = collect_soft_rule_warnings(pack)
    parts.append(_rules_appendix(soft_warnings))
    body_so_far = "\n".join(parts)
    numeric_warnings = collect_numeric_fidelity_warnings(body_so_far, pack)
    parts.append(_numeric_appendix(numeric_warnings))
    parts.append(_pack_appendix(pack))
    return "\n".join(parts).strip() + "\n", cite_pool

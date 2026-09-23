"""Phase 2: per-section drafting with sliding summary window (no full-doc prompt)."""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from .storm_schema import ReportOutline, SectionDraft, SectionSpec

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str, str, float, dict | None], None]

_WORD_RE = re.compile(r"\S+")


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def _summarize(text: str, *, limit: int = 180) -> str:
    flat = re.sub(r"\s+", " ", (text or "").strip())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


def _pack_excerpt_for_section(spec: SectionSpec, pack: dict[str, Any]) -> str:
    """Read-only numeric/table hints — must not be rewritten by the model."""
    lines: list[str] = ["【确定性摘录（禁止改写数字）】"]
    sid = spec.section_id
    if "background" in sid or "风险" in spec.title:
        for r in ((pack.get("requirements") or {}).get("rows") or [])[:6]:
            if isinstance(r, dict):
                lines.append(
                    f"- {r.get('metric')}: {r.get('value')} {r.get('unit') or ''} "
                    f"({r.get('direction') or ''})"
                )
    if "formula" in sid:
        for r in ((pack.get("formula") or {}).get("rows") or [])[:8]:
            if isinstance(r, dict):
                lines.append(
                    f"- {r.get('name')} / {r.get('role')} / {r.get('weight_pct')}% / "
                    f"CAS {r.get('cas') or '-'}"
                )
    if "doe" in sid or "lab" in sid:
        plans = (pack.get("doe") or {}).get("plans") or []
        if plans and isinstance(plans[0], dict):
            p0 = plans[0]
            lines.append(
                f"- DOE: {p0.get('design_type')} bounds={p0.get('bounds')} id={p0.get('plan_id')}"
            )
        for r in ((pack.get("lab") or {}).get("rows") or [])[:4]:
            if isinstance(r, dict):
                lines.append(
                    f"- Lab {r.get('item')}: {r.get('metric')}={r.get('value')} "
                    f"(source={r.get('source')})"
                )
    if "loop" in sid:
        for h in ((pack.get("loop") or {}).get("history") or [])[:4]:
            if isinstance(h, dict):
                lines.append(f"- loop: {h}")
    if "literature" in sid:
        for r in ((pack.get("literature") or {}).get("rows") or [])[:5]:
            if isinstance(r, dict):
                lines.append(
                    f"- lit: {r.get('title')} id={r.get('source_id')}"
                )
    if len(lines) == 1:
        lines.append("- （本章暂无对应 Pack 切片，仅依据大纲与检索意图撰写。）")
    return "\n".join(lines)


def _outline_tree(outline: ReportOutline, current_id: str) -> str:
    lines = [f"主题: {outline.topic}", "大纲:"]
    for i, s in enumerate(outline.sections, 1):
        mark = "◀" if s.section_id == current_id else "·"
        lines.append(f"  {mark} {i}. [{s.section_id}] {s.title} — {s.core_intent[:60]}")
    return "\n".join(lines)


def collect_section_evidence(
    spec: SectionSpec,
    pack: dict[str, Any],
    *,
    project_id: str,
    top_k: int = 4,
) -> list[dict[str, str]]:
    """Best-effort retrieval for section queries; never raises."""
    hits: list[dict[str, str]] = []
    # Prefer pack literature as grounded ids
    for r in ((pack.get("literature") or {}).get("rows") or [])[:top_k]:
        if isinstance(r, dict) and r.get("source_id"):
            hits.append(
                {
                    "id": str(r.get("source_id")),
                    "title": str(r.get("title") or r.get("source_id")),
                    "snippet": str(r.get("snippet") or "")[:240],
                }
            )
    try:
        from ...services import kb_index

        if kb_index.kb_enabled():
            for q in (spec.retrieval_queries or [])[:3]:
                for ev in kb_index.search_chunks(q, k=2, project_id=project_id) or []:
                    ident = getattr(ev, "identifier", None) or getattr(ev, "id", None) or ""
                    hits.append(
                        {
                            "id": str(ident),
                            "title": str(getattr(ev, "title", "") or ident)[:120],
                            "snippet": str(getattr(ev, "snippet", "") or "")[:240],
                        }
                    )
                    if len(hits) >= top_k * 2:
                        break
    except Exception as exc:
        logger.debug("storm section retrieve soft-failed: %s", exc)

    # de-dupe by id
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for h in hits:
        key = h.get("id") or h.get("title") or ""
        if key in seen:
            continue
        seen.add(str(key))
        out.append(h)
        if len(out) >= top_k:
            break
    return out


def draft_section_deterministic(
    spec: SectionSpec,
    outline: ReportOutline,
    pack: dict[str, Any],
    *,
    prev_summary: str = "",
    evidence: list[dict[str, str]] | None = None,
) -> SectionDraft:
    """Template draft from pack + outline — offline safe."""
    evidence = evidence or []
    cite_ids = [e["id"] for e in evidence if e.get("id")]
    body_parts = [
        f"## {spec.title}",
        "",
        f"> 本章意图：{spec.core_intent or '（未标注）'}",
        "",
    ]
    if prev_summary:
        body_parts += ["**承上（滑动摘要）**", "", prev_summary, ""]
    body_parts += [_pack_excerpt_for_section(spec, pack), ""]
    if evidence:
        body_parts.append("**检索线索**")
        body_parts.append("")
        for i, e in enumerate(evidence, 1):
            body_parts.append(f"- [^{i}] {e.get('title')} (`{e.get('id')}`): {e.get('snippet')}")
            body_parts.append("")
    body_parts += [
        "### 论述要点",
        "",
        f"围绕「{outline.topic}」展开，聚焦 {', '.join(spec.focal_entities) or '本章主题'}。",
        "数值以卷宗确定性表为准；本节为研发草稿（draft_not_claims），需人工审阅。",
        "",
    ]
    md = "\n".join(body_parts)
    return SectionDraft(
        section_id=spec.section_id,
        content_markdown=md,
        used_citations=cite_ids[:12],
        summary=_summarize(f"{spec.title}：{spec.core_intent}。{prev_summary}"),
        word_count=_word_count(md),
        model="",
        source="deterministic",
    )


def draft_section(
    spec: SectionSpec,
    outline: ReportOutline,
    pack: dict[str, Any],
    *,
    project_id: str,
    prev_summary: str = "",
    use_llm: bool = False,
) -> SectionDraft:
    evidence = collect_section_evidence(spec, pack, project_id=project_id)
    base = draft_section_deterministic(
        spec, outline, pack, prev_summary=prev_summary, evidence=evidence
    )
    if not use_llm:
        return base

    try:
        from ...services.citation_binder import format_citation_list
        from ...services.llm import complete_structured
        from .storm_polish import evidence_to_anchors

        anchors = evidence_to_anchors(evidence)
        cite_block = format_citation_list(anchors) if anchors else "_无可引用的文献。_"

        system = (
            "你是工业配方技术报告撰稿人。只写当前章节 Markdown。"
            "禁止改写「确定性摘录」中的数字；可用「见表/见摘录」引用。"
            "引用时严格使用下方可引用文献的 [^n]；不得编造 DOI/文献编号。"
            "另输出 150–200 字 summary 供下一章滑动窗口。"
            "输出严格 JSON：section_id, content_markdown, used_citations, summary。"
        )
        user = (
            f"{_outline_tree(outline, spec.section_id)}\n\n"
            f"当前章节 id={spec.section_id} title={spec.title}\n"
            f"意图: {spec.core_intent}\n"
            f"目标字数: {spec.target_word_count}\n"
            f"上一章摘要: {prev_summary or '（首章）'}\n\n"
            f"{_pack_excerpt_for_section(spec, pack)}\n\n"
            f"**可引用文献**（严格按照编号引用）：\n{cite_block}\n"
        )
        parsed, err = complete_structured(system, user, SectionDraft, retry=True)
        if parsed is None:
            logger.warning("storm draft LLM failed for %s: %s", spec.section_id, err)
            return base
        parsed.section_id = spec.section_id
        parsed.source = "llm"
        if not parsed.summary:
            parsed.summary = _summarize(parsed.content_markdown)
        parsed.word_count = _word_count(parsed.content_markdown)
        if not parsed.used_citations:
            parsed.used_citations = list(base.used_citations)
        return parsed
    except Exception as exc:
        logger.warning("storm draft LLM exception %s: %s", spec.section_id, exc)
        return base


def draft_all_sections(
    outline: ReportOutline,
    pack: dict[str, Any],
    *,
    project_id: str,
    use_llm: bool = False,
    progress_cb: ProgressCb | None = None,
) -> dict[str, SectionDraft]:
    """Sequential drafting (MVP): honor depends_on order via section list order."""
    drafts: dict[str, SectionDraft] = {}
    n = max(len(outline.sections), 1)
    prev_summary = ""
    for i, spec in enumerate(outline.sections):
        # Prefer summary from last declared dependency if available
        for dep in spec.depends_on:
            if dep in drafts and drafts[dep].summary:
                prev_summary = drafts[dep].summary
                break
        stage = f"drafting_section_{i + 1}"
        prog = 0.2 + 0.55 * (i / n)
        if progress_cb:
            progress_cb(
                stage,
                f"正在撰写：{spec.title}",
                prog,
                {"section_id": spec.section_id, "index": i + 1, "total": n},
            )
        draft = draft_section(
            spec,
            outline,
            pack,
            project_id=project_id,
            prev_summary=prev_summary,
            use_llm=use_llm,
        )
        drafts[spec.section_id] = draft
        prev_summary = draft.summary or prev_summary
    return drafts

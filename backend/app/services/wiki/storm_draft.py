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
    top_k: int | None = None,
    exclude_ids: set[str] | None = None,
) -> list[dict[str, str]]:
    """Best-effort retrieval for section queries; never raises.

    Wave A: hybrid façade + thicker caps (settings.wiki_storm_section_*).
    Wave D: ``exclude_ids`` skips chunks already used by earlier sections.
    """
    from ...config import get_settings

    settings = get_settings()
    query_k = max(1, int(getattr(settings, "wiki_storm_section_query_k", 6) or 6))
    cap = int(top_k if top_k is not None else getattr(settings, "wiki_storm_section_evidence_cap", 10) or 10)
    cap = max(1, min(cap, 24))
    skip = exclude_ids or set()
    hits: list[dict[str, str]] = []
    # Prefer pack literature as grounded ids
    for r in ((pack.get("literature") or {}).get("rows") or [])[:cap]:
        if isinstance(r, dict) and r.get("source_id"):
            hits.append(
                {
                    "id": str(r.get("source_id")),
                    "title": str(r.get("title") or r.get("source_id")),
                    "snippet": str(r.get("snippet") or "")[:400],
                    "relevance": str(r.get("relevance") or r.get("score") or ""),
                    "page": str(r.get("page") or r.get("page_no") or ""),
                }
            )
    try:
        from ...services import kb_index

        if kb_index.kb_enabled():
            pool: list = []
            for q in (spec.retrieval_queries or [])[:3]:
                pool.extend(
                    kb_index.retrieve_evidence(
                        q, k=query_k, project_id=project_id, mode="hybrid"
                    )
                    or []
                )
            if pool and bool(getattr(settings, "wiki_storm_section_rerank", False)):
                try:
                    from ...services.rag import rerank_scored

                    q0 = (spec.retrieval_queries or ["section"])[0]
                    ranked, _meta = rerank_scored(
                        q0, pool, k=min(cap * 2, len(pool)), prefer="cross_encoder"
                    )
                    pool = [it.evidence for it in ranked] if ranked else pool
                except Exception as exc:
                    logger.debug("storm section rerank soft-failed: %s", exc)
            for ev in pool:
                ident = getattr(ev, "identifier", None) or getattr(ev, "id", None) or ""
                page = getattr(ev, "page", None)
                hits.append(
                    {
                        "id": str(ident),
                        "title": str(getattr(ev, "title", "") or ident)[:120],
                        "snippet": str(getattr(ev, "snippet", "") or "")[:400],
                        "relevance": str(getattr(ev, "relevance", "") or ""),
                        "page": str(page) if page is not None else "",
                    }
                )
    except Exception as exc:
        logger.debug("storm section retrieve soft-failed: %s", exc)

    # de-dupe by id (prefer first / higher-ranked); skip cross-section pool
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for h in hits:
        key = h.get("id") or h.get("title") or ""
        if not key or key in seen or key in skip:
            continue
        seen.add(str(key))
        out.append(h)
        if len(out) >= cap:
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


def _glossary_from_pack(pack: dict[str, Any]) -> list[str]:
    """Wave C: compact terminology list for prompt injection (not Claims)."""
    terms: list[str] = []
    for r in ((pack.get("formula") or {}).get("rows") or [])[:12]:
        if isinstance(r, dict) and r.get("name"):
            cas = (r.get("cas") or "").strip()
            role = (r.get("role") or "").strip()
            label = str(r["name"])
            if cas:
                label = f"{label} (CAS {cas})"
            if role:
                label = f"{label} [{role}]"
            terms.append(label)
    for r in ((pack.get("requirements") or {}).get("rows") or [])[:8]:
        if isinstance(r, dict) and r.get("metric"):
            terms.append(str(r["metric"]))
    # stable unique
    out: list[str] = []
    seen: set[str] = set()
    for t in terms:
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out[:20]


def draft_section(
    spec: SectionSpec,
    outline: ReportOutline,
    pack: dict[str, Any],
    *,
    project_id: str,
    prev_summary: str = "",
    use_llm: bool = False,
    exclude_ids: set[str] | None = None,
) -> SectionDraft:
    evidence = collect_section_evidence(
        spec, pack, project_id=project_id, exclude_ids=exclude_ids
    )
    base = draft_section_deterministic(
        spec, outline, pack, prev_summary=prev_summary, evidence=evidence
    )
    glossary = _glossary_from_pack(pack)
    if glossary and base.content_markdown:
        gloss_line = "；".join(glossary[:12])
        inject = f"\n\n> 规范术语（勿同物异名）：{gloss_line}\n"
        # Insert after first heading block
        parts = base.content_markdown.split("\n", 2)
        if len(parts) >= 2:
            base.content_markdown = parts[0] + "\n" + parts[1] + inject + (parts[2] if len(parts) > 2 else "")
        else:
            base.content_markdown = base.content_markdown + inject
    if not use_llm:
        return base

    try:
        from ...services.citation_binder import build_citation_prompt
        from ...services.llm import complete_structured
        from .storm_polish import evidence_to_anchors

        anchors = evidence_to_anchors(evidence)
        # P2: wire production STORM draft through build_citation_prompt
        # (shared [^n] contract with chat / binder), then append JSON schema.
        cite_frame = build_citation_prompt(
            question=(
                f"撰写技术报告章节「{spec.title}」：{spec.core_intent}"
            ),
            anchors=anchors,
            system_instruction=(
                "你是工业配方技术报告撰稿人。只写当前章节 Markdown。"
                "禁止改写「确定性摘录」中的数字；可用「见表/见摘录」引用。"
                "另输出 150–200 字 summary 供下一章滑动窗口。"
            ),
        )
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
            f"{cite_frame}\n"
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


def plan_draft_waves(sections: list[SectionSpec]) -> list[list[SectionSpec]]:
    """Partition sections into waves where each wave's depends_on ⊆ prior waves.

    Unknown / cyclic depends_on fall back to single-section waves in outline order
    so drafting never deadlocks.
    """
    if not sections:
        return []
    by_id = {s.section_id: s for s in sections}
    remaining = list(sections)
    done: set[str] = set()
    waves: list[list[SectionSpec]] = []
    guard = 0
    while remaining:
        guard += 1
        if guard > len(sections) + 2:
            # Pathological cycle — drain one-by-one
            waves.append([remaining.pop(0)])
            done.add(waves[-1][0].section_id)
            continue
        ready: list[SectionSpec] = []
        for s in remaining:
            deps = [d for d in (s.depends_on or []) if d in by_id]
            if all(d in done for d in deps):
                ready.append(s)
        if not ready:
            # Missing dep or cycle — force next in outline order
            ready = [remaining[0]]
        ready_ids = {s.section_id for s in ready}
        remaining = [s for s in remaining if s.section_id not in ready_ids]
        waves.append(ready)
        done.update(ready_ids)
    return waves


def _prev_summary_for(
    spec: SectionSpec,
    drafts: dict[str, SectionDraft],
    *,
    fallback: str = "",
) -> str:
    for dep in spec.depends_on or []:
        if dep in drafts and drafts[dep].summary:
            return drafts[dep].summary
    return fallback


def draft_all_sections(
    outline: ReportOutline,
    pack: dict[str, Any],
    *,
    project_id: str,
    use_llm: bool = False,
    parallel: bool = False,
    max_workers: int = 3,
    progress_cb: ProgressCb | None = None,
) -> dict[str, SectionDraft]:
    """Draft sections in depends_on waves; optionally parallel within each wave."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    drafts: dict[str, SectionDraft] = {}
    sections = list(outline.sections)
    n = max(len(sections), 1)
    waves = plan_draft_waves(sections)
    completed = 0
    progress_lock = threading.Lock()
    last_summary = ""
    # Wave D: global evidence pool — later sections skip earlier chunk ids.
    used_evidence_ids: set[str] = set()
    used_lock = threading.Lock()

    workers = max(1, min(int(max_workers or 1), 8))
    use_pool = bool(parallel) and workers > 1

    def _draft_one(spec: SectionSpec, prev: str) -> SectionDraft:
        with used_lock:
            exclude = set(used_evidence_ids)
        d = draft_section(
            spec,
            outline,
            pack,
            project_id=project_id,
            prev_summary=prev,
            use_llm=use_llm,
            exclude_ids=exclude,
        )
        with used_lock:
            for cid in d.used_citations or []:
                if cid:
                    used_evidence_ids.add(str(cid))
        return d

    def _emit(spec: SectionSpec, index: int) -> None:
        if not progress_cb:
            return
        stage = f"drafting_section_{index}"
        prog = 0.2 + 0.55 * ((index - 1) / n)
        with progress_lock:
            progress_cb(
                stage,
                f"正在撰写：{spec.title}",
                prog,
                {
                    "section_id": spec.section_id,
                    "index": index,
                    "total": n,
                    "parallel": use_pool,
                    "wave_size": None,
                },
            )

    for wave_i, wave in enumerate(waves):
        # Snapshot summaries from prior waves only (thread-safe for this wave)
        summary_by_spec: dict[str, str] = {}
        for spec in wave:
            summary_by_spec[spec.section_id] = _prev_summary_for(
                spec, drafts, fallback=last_summary
            )

        if progress_cb:
            with progress_lock:
                progress_cb(
                    f"drafting_wave_{wave_i + 1}",
                    f"分章波次 {wave_i + 1}/{len(waves)}（{len(wave)} 章）",
                    0.2 + 0.55 * (completed / n),
                    {
                        "wave": wave_i + 1,
                        "wave_total": len(waves),
                        "section_ids": [s.section_id for s in wave],
                        "parallel": use_pool and len(wave) > 1,
                    },
                )

        if use_pool and len(wave) > 1:
            with ThreadPoolExecutor(
                max_workers=min(workers, len(wave)),
                thread_name_prefix="storm-draft",
            ) as pool:
                futures = {}
                for spec in wave:
                    completed += 1
                    idx = completed
                    _emit(spec, idx)
                    fut = pool.submit(
                        _draft_one, spec, summary_by_spec[spec.section_id]
                    )
                    futures[fut] = spec
                for fut in as_completed(futures):
                    spec = futures[fut]
                    drafts[spec.section_id] = fut.result()
        else:
            for spec in wave:
                completed += 1
                _emit(spec, completed)
                drafts[spec.section_id] = _draft_one(
                    spec, summary_by_spec[spec.section_id]
                )

        # Advance sliding fallback for next wave (outline order within wave)
        for spec in wave:
            if drafts[spec.section_id].summary:
                last_summary = drafts[spec.section_id].summary

    return drafts

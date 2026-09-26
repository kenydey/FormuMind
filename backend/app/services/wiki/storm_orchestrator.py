"""STORM longform report orchestrator (flag-gated L2 draft)."""
from __future__ import annotations

import logging
from typing import Any, Callable

from ...config import get_settings
from ...db.wiki_store import get_wiki_store
from .dossier import ensure_project_dossier, get_dossier_pack
from .schema import dump_page, project_report_path, safe_key
from .storm_draft import draft_all_sections
from .storm_outline import generate_outline, persist_outline_sidecar
from .storm_polish import stitch_and_polish
from .storm_schema import StormReportState

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str, str, float, dict | None], None]


def _require_storm_enabled() -> None:
    settings = get_settings()
    if not settings.wiki_enabled:
        raise PermissionError("wiki_enabled is false")
    if not getattr(settings, "wiki_project_dossier_enabled", False):
        raise PermissionError("wiki_project_dossier_enabled is false")
    if not getattr(settings, "wiki_dossier_report_enabled", False):
        raise PermissionError("wiki_dossier_report_enabled is false")
    if not getattr(settings, "wiki_storm_report_enabled", False):
        raise PermissionError("wiki_storm_report_enabled is false")


def _pack_to_evidence(pack: dict[str, Any], cite_ids: list[str]) -> list:
    """Map dossier pack / citation ids → Evidence for claim_checker (fail-open).

    Wave A: prefer real relevance / raw snippet / page over hardcoded 0.5.
    """
    from ...domain.schemas import Evidence

    sources = pack.get("sources") or pack.get("evidence") or []
    by_id: dict[str, Any] = {}
    if isinstance(sources, list):
        for i, row in enumerate(sources):
            if not isinstance(row, dict):
                continue
            sid = str(row.get("source_id") or row.get("id") or "").strip()
            if sid:
                by_id[sid] = row
                by_id.setdefault(f"_ord_{i}", row)
    # Literature rows often carry better snippets than dossier summary.
    for r in ((pack.get("literature") or {}).get("rows") or []):
        if isinstance(r, dict) and r.get("source_id"):
            sid = str(r["source_id"])
            by_id.setdefault(sid, r)
            prev = by_id.get(sid) or {}
            if not prev.get("snippet") and r.get("snippet"):
                by_id[sid] = {**prev, **{k: v for k, v in r.items() if v}}

    out: list[Evidence] = []
    for rank, sid in enumerate(cite_ids or []):
        row = by_id.get(str(sid)) or {}
        title = str(row.get("title") or sid)[:200]
        # Prefer raw excerpt fields over compressed summary.
        snippet = str(
            row.get("snippet")
            or row.get("text")
            or row.get("excerpt")
            or row.get("summary")
            or title
        )[:1200]
        rel_raw = row.get("relevance")
        if rel_raw is None:
            rel_raw = row.get("score")
        try:
            relevance = float(rel_raw) if rel_raw is not None else max(0.2, 0.9 - 0.05 * rank)
        except (TypeError, ValueError):
            relevance = max(0.2, 0.9 - 0.05 * rank)
        relevance = max(0.05, min(1.0, relevance))
        page = row.get("page")
        if page is None:
            page = row.get("page_no")
        try:
            page_i = int(page) if page is not None else None
        except (TypeError, ValueError):
            page_i = None
        out.append(
            Evidence(
                source=str(row.get("source_kind") or row.get("source") or "kb"),
                identifier=str(sid),
                title=title,
                snippet=snippet,
                relevance=relevance,
                page=page_i,
            )
        )
    if not out:
        # Fallback: use pack narrative bits so offline checker still runs.
        summary = str(pack.get("summary") or pack.get("topic") or "storm report")[:800]
        out.append(
            Evidence(
                source="dossier",
                identifier="dossier:pack",
                title="DossierPack",
                snippet=summary,
                relevance=0.3,
            )
        )
    return out


def _storm_claim_check(
    topic: str,
    markdown: str,
    pack: dict[str, Any],
    cite_ids: list[str],
) -> dict[str, Any]:
    """Run claim_checker; return meta (+ optional rewritten markdown)."""
    from ...pipeline.claim_checker import append_verification_footer, check_claims

    evidence = _pack_to_evidence(pack, cite_ids)
    result = check_claims(topic or "STORM report", markdown, evidence)
    updated = append_verification_footer(markdown, result)
    return {
        "applied": True,
        "engine": result.engine,
        "pass_rate": result.pass_rate,
        "claim_check_passed": result.claim_check_passed,
        "needs_regenerate": result.needs_regenerate,
        "claim_count": len(result.claims),
        "failed_claim_texts": [
            v.text for v in result.claims if v.verdict.value != "supported"
        ][:8],
        "markdown": updated,
    }


def _maybe_regenerate_failed_sections(
    *,
    outline: Any,
    drafts: dict[str, Any],
    pack: dict[str, Any],
    project_id: str,
    use_llm: bool,
    claim_meta: dict[str, Any],
    markdown: str,
    cite_ids: list[str],
    progress_cb: ProgressCb | None = None,
) -> tuple[str, list[str], dict[str, Any]]:
    """Wave B: one-shot section regenerate when needs_regenerate (flag-gated)."""
    from ...config import get_settings

    settings = get_settings()
    if not bool(getattr(settings, "wiki_storm_claim_regenerate", False)):
        return markdown, cite_ids, claim_meta
    if not claim_meta.get("needs_regenerate"):
        return markdown, cite_ids, claim_meta

    failed_texts = claim_meta.get("failed_claim_texts") or []
    if not failed_texts or not drafts:
        return markdown, cite_ids, claim_meta

    # Heuristic: regenerate the first section whose draft mentions a failed claim.
    target_id = None
    for sid, draft in drafts.items():
        body = getattr(draft, "content_markdown", "") or ""
        if any(t[:40] in body for t in failed_texts if t):
            target_id = sid
            break
    if not target_id:
        # Fall back to last content section (often risks / open questions).
        secs = getattr(outline, "sections", None) or []
        target_id = secs[-1].section_id if secs else None
    if not target_id or target_id not in drafts:
        claim_meta = {**claim_meta, "regenerate": {"applied": False, "reason": "no_target"}}
        return markdown, cite_ids, claim_meta

    try:
        from .storm_draft import draft_section
        from .storm_polish import stitch_and_polish

        spec = next(s for s in outline.sections if s.section_id == target_id)
        if progress_cb:
            progress_cb("claim_regenerate", f"定向再生章节 {target_id}…", 0.88, None)
        new_draft = draft_section(
            spec,
            outline,
            pack,
            project_id=project_id,
            prev_summary="",
            use_llm=use_llm,
        )
        # Soft prepend evidence-insufficiency note — draft_not_claims only.
        note = (
            "\n\n> 论断核验：本章已按证据不足提示改写一轮（`wiki_storm_claim_regenerate`）；"
            "仍不进 Claims / DOE。\n"
        )
        new_draft.content_markdown = (new_draft.content_markdown or "") + note
        drafts[target_id] = new_draft
        markdown2, cite_ids2 = stitch_and_polish(outline, drafts, pack, progress_cb=None)
        claim_meta2 = _storm_claim_check(outline.topic, markdown2, pack, cite_ids2)
        if claim_meta2.get("markdown"):
            markdown2 = str(claim_meta2.pop("markdown"))
        claim_meta2["regenerate"] = {
            "applied": True,
            "section_id": target_id,
            "rounds": 1,
        }
        # Never loop: even if still needs_regenerate, stop after one round.
        claim_meta2["needs_regenerate"] = False
        return markdown2, cite_ids2, claim_meta2
    except Exception as exc:
        logger.warning("STORM claim regenerate soft-failed: %s", exc)
        claim_meta = {
            **claim_meta,
            "regenerate": {"applied": False, "error": str(exc)},
        }
        return markdown, cite_ids, claim_meta


def run_storm_report(
    project_id: str,
    *,
    topic: str = "",
    max_sections: int | None = None,
    perspectives: list[str] | None = None,
    use_llm: bool = False,
    parallel: bool | None = None,
    max_workers: int | None = None,
    ensure_dossier: bool = True,
    persist: bool = True,
    campaign_id: str | None = None,
    task_id: str = "",
    progress_cb: ProgressCb | None = None,
) -> dict[str, Any]:
    """Full Pre-writing → Writing → Polish pipeline."""
    _require_storm_enabled()
    pid = (project_id or "").strip()
    if not pid:
        raise ValueError("project_id required")

    settings = get_settings()
    max_sec = int(
        max_sections
        if max_sections is not None
        else getattr(settings, "wiki_storm_max_sections", 6) or 6
    )
    max_sec = max(3, min(max_sec, 12))
    parallel_use = (
        bool(parallel)
        if parallel is not None
        else bool(getattr(settings, "wiki_storm_parallel", False))
    )
    workers = int(
        max_workers
        if max_workers is not None
        else getattr(settings, "wiki_storm_parallel_workers", 3) or 3
    )
    workers = max(1, min(workers, 8))

    state = StormReportState(project_id=pid, task_id=task_id or "", stage="generating_outline")

    if ensure_dossier:
        try:
            ensure_project_dossier(pid, campaign_id=campaign_id)
        except Exception as exc:
            logger.debug("storm ensure dossier soft-failed: %s", exc)

    pack = get_dossier_pack(pid, campaign_id=campaign_id)
    outline = generate_outline(
        pack,
        project_id=pid,
        topic=topic,
        max_sections=max_sec,
        perspectives=perspectives,
        use_llm=use_llm,
        progress_cb=progress_cb,
    )
    state.outline = outline
    outline_rel = ""
    try:
        outline_rel = persist_outline_sidecar(pid, outline)
    except Exception as exc:
        logger.debug("storm outline sidecar soft-failed: %s", exc)

    drafts = draft_all_sections(
        outline,
        pack,
        project_id=pid,
        use_llm=use_llm,
        parallel=parallel_use,
        max_workers=workers,
        progress_cb=progress_cb,
    )
    state.drafts = drafts

    markdown, cite_ids = stitch_and_polish(
        outline, drafts, pack, progress_cb=progress_cb
    )
    claim_meta: dict[str, Any] = {"applied": False}
    if getattr(settings, "wiki_storm_claim_check", True):
        try:
            claim_meta = _storm_claim_check(outline.topic, markdown, pack, cite_ids)
            if claim_meta.get("markdown"):
                markdown = str(claim_meta.pop("markdown"))
            markdown, cite_ids, claim_meta = _maybe_regenerate_failed_sections(
                outline=outline,
                drafts=drafts,
                pack=pack,
                project_id=pid,
                use_llm=use_llm,
                claim_meta=claim_meta,
                markdown=markdown,
                cite_ids=cite_ids,
                progress_cb=progress_cb,
            )
        except Exception as exc:
            logger.warning("STORM claim_check soft-failed (fail-open): %s", exc)
            claim_meta = {"applied": False, "error": str(exc)}
    path = project_report_path(pid, "storm")
    state.final_path = path
    state.final_markdown = markdown
    state.stage = "done"
    state.meta = {
        "outline_path": outline_rel,
        "section_count": len(outline.sections),
        "citation_count": len(cite_ids),
        "use_llm": bool(use_llm),
        "outline_source": outline.source,
        "parallel": parallel_use,
        "parallel_workers": workers if parallel_use else 1,
        "claim_check": claim_meta,
    }

    out: dict[str, Any] = {
        "ok": True,
        "project_id": pid,
        "path": path,
        "title": outline.topic,
        "markdown": markdown,
        "outline": outline.model_dump(mode="json"),
        "section_count": len(outline.sections),
        "source_ids": cite_ids,
        "disclaimer": "draft_not_claims",
        "outline_path": outline_rel,
        "meta": state.meta,
    }

    if not persist:
        return out

    store = get_wiki_store()
    flags = ["unreviewed", "report", "draft", "storm"]
    if use_llm:
        flags.append("llm_draft")
    title = f"STORM 长文 · {outline.topic}"
    extra = {
        "template": "report_storm",
        "report_template": "storm",
        "schema_version": 1,
        "llm_generated": bool(use_llm),
        "reviewed": False,
        "project_id": pid,
        "campaign_id": pack.get("campaign_id") or "",
        "disclaimer": "draft_not_claims",
        "storm_section_count": len(outline.sections),
        "storm_outline_source": outline.source,
    }
    md = dump_page(
        kind="report",
        title=title,
        entity_id=f"report:storm:{safe_key(pid)}"[:64],
        norm_key=f"{safe_key(pid)}-storm"[:80],
        source_ids=cite_ids[:40],
        flags=flags,
        summary=markdown[:400],
        evidence_blocks=[markdown],
        extra_meta=extra,
    )
    # Replace body after front-matter with composed markdown
    if md.startswith("---"):
        end = md.find("\n---", 3)
        if end > 0:
            md = md[: end + 4] + "\n\n" + markdown
    store.upsert_page(
        path=path,
        kind="report",
        title=title,
        norm_key=f"{safe_key(pid)}-storm"[:80],
        entity_id=f"report:storm:{safe_key(pid)}"[:64],
        markdown=md,
        source_ids=cite_ids[:40],
        flags=flags,
    )
    out["title"] = title
    out["persisted"] = True
    if progress_cb:
        progress_cb("done", "STORM 长文已落盘", 1.0, {"path": path})
    return out


def export_storm_report(
    project_id: str,
    fmt: str,
    *,
    regenerate: bool = False,
    topic: str = "",
    use_llm: bool = False,
    parallel: bool | None = None,
    max_workers: int | None = None,
    ensure_dossier: bool = True,
    campaign_id: str | None = None,
) -> dict[str, Any]:
    """Export persisted STORM markdown (or regenerate first) to md/docx/pdf/pptx."""
    from .report_export import export_bytes, export_capabilities
    from .schema import project_report_path, safe_key

    _require_storm_enabled()
    pid = (project_id or "").strip()
    if not pid:
        raise ValueError("project_id required")

    caps = export_capabilities()
    kind = (fmt or "md").strip().lower()
    if kind in ("ppt", "deck"):
        kind = "pptx"
    if kind == "markdown":
        kind = "md"
    if kind not in caps or not caps.get(kind):
        raise RuntimeError(f"export format unavailable: {fmt}; caps={caps}")

    path = project_report_path(pid, "storm")
    store = get_wiki_store()
    row = store.get_by_path(path)
    title = ""
    markdown = ""

    if regenerate or row is None:
        generated = run_storm_report(
            pid,
            topic=topic,
            use_llm=use_llm,
            parallel=parallel,
            max_workers=max_workers,
            ensure_dossier=ensure_dossier,
            persist=True,
            campaign_id=campaign_id,
        )
        title = str(generated.get("title") or "STORM 长文")
        markdown = str(generated.get("markdown") or "")
        path = str(generated.get("path") or path)
    else:
        title = str(row.title or f"STORM · {pid}")
        markdown = store.read_markdown(row.path) or ""
        if not markdown.strip():
            raise LookupError("storm report markdown empty")

    payload, media_type, ext = export_bytes(markdown, kind, title=title)
    filename = f"project-{safe_key(pid)}-storm.{ext}"
    return {
        "ok": True,
        "format": kind,
        "filename": filename,
        "media_type": media_type,
        "bytes": payload,
        "path": path,
        "title": title,
        "size": len(payload),
        "capabilities": caps,
        "disclaimer": "draft_not_claims",
        "regenerated": bool(regenerate or row is None),
    }

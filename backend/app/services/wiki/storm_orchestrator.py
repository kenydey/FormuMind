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

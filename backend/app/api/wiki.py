"""LLM Wiki read APIs (W1)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..config import get_settings
from ..db.wiki_store import get_wiki_store

router = APIRouter(prefix="/wiki", tags=["wiki"])


class WikiPageItem(BaseModel):
    id: str
    path: str
    kind: str
    title: str
    norm_key: str = ""
    entity_id: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    revision: int = 1
    updated_at: str | None = None


class WikiPageDetail(WikiPageItem):
    markdown: str = ""


class WikiPagesResponse(BaseModel):
    pages: list[WikiPageItem]
    total: int


def _require_wiki() -> None:
    if not get_settings().wiki_enabled:
        raise HTTPException(status_code=409, detail="LLM Wiki 未启用（FORMUMIND_WIKI_ENABLED）")


def _item(row) -> WikiPageItem:
    return WikiPageItem(
        id=row.id,
        path=row.path,
        kind=row.kind,
        title=row.title or "",
        norm_key=row.norm_key or "",
        entity_id=row.entity_id,
        source_ids=list(row.source_ids or []),
        flags=list(row.flags or []),
        revision=int(row.revision or 1),
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


@router.get("/pages", response_model=WikiPagesResponse)
def list_pages(
    kind: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    project_id: str | None = Query(
        default=None,
        description="When set, only dossier/reports for this project and wiki pages "
        "whose source_ids belong to the project (strict; no global leak)",
    ),
) -> WikiPagesResponse:
    _require_wiki()
    store = get_wiki_store()
    if project_id:
        from ..services.wiki.project_scope import filter_wiki_rows

        # Over-fetch then filter — wiki has no project_id column.
        scan = store.list_pages(kind=kind, limit=min(500, max(limit + offset, limit * 5)), offset=0)
        scoped = filter_wiki_rows(scan, project_id)
        rows = scoped[offset : offset + limit]
        return WikiPagesResponse(pages=[_item(r) for r in rows], total=len(scoped))
    rows = store.list_pages(kind=kind, limit=limit, offset=offset)
    return WikiPagesResponse(pages=[_item(r) for r in rows], total=len(rows))


@router.get("/pages/{page_id}", response_model=WikiPageDetail)
def get_page(page_id: str) -> WikiPageDetail:
    _require_wiki()
    store = get_wiki_store()
    row = store.get(page_id)
    if row is None:
        raise HTTPException(status_code=404, detail="wiki page not found")
    md = store.read_markdown(row.path) or ""
    base = _item(row)
    return WikiPageDetail(**base.model_dump(), markdown=md)


@router.get("/by-path", response_model=WikiPageDetail)
def get_by_path(path: str = Query(min_length=1)) -> WikiPageDetail:
    _require_wiki()
    store = get_wiki_store()
    row = store.get_by_path(path)
    if row is None:
        raise HTTPException(status_code=404, detail="wiki page not found")
    md = store.read_markdown(row.path) or ""
    base = _item(row)
    return WikiPageDetail(**base.model_dump(), markdown=md)


class WikiFlagAction(BaseModel):
    id: str
    label: str
    hint: str = ""
    target: str = ""
    # S5: explicit apply-broken payload (only set on apply_broken_fix_* chips)
    broken: str = ""
    replacement_path: str = ""
    mode: str = ""


class WikiFlagItem(BaseModel):
    id: str
    path: str
    kind: str
    title: str = ""
    norm_key: str = ""
    flags: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    actions: list[WikiFlagAction] = Field(default_factory=list)


class WikiFlagsResponse(BaseModel):
    pages: list[WikiFlagItem]


class WikiLintRunRequest(BaseModel):
    limit: int = Field(default=200, ge=1, le=500)
    detect_orphan: bool = True


class WikiLintSweepRequest(BaseModel):
    limit: int = Field(default=200, ge=1, le=500)
    detect_orphan: bool = True


class WikiApplyBrokenRequest(BaseModel):
    path: str = Field(min_length=1)
    broken: str = Field(min_length=1)
    replacement_path: str = Field(min_length=1)
    mode: str = Field(default="rewrite", description="rewrite | append_related")

@router.get("/flags", response_model=WikiFlagsResponse)
def list_flagged(
    limit: int = Query(default=50, ge=1, le=200),
    project_id: str | None = Query(default=None),
) -> WikiFlagsResponse:
    _require_wiki()
    from ..services.wiki.lint import list_flagged_pages
    from ..services.wiki.project_scope import wiki_page_in_project, project_source_id_set

    rows = list_flagged_pages(limit=limit if not project_id else min(500, max(limit * 5, limit)))
    if project_id:
        allowed = project_source_id_set(project_id)
        rows = [
            r
            for r in rows
            if wiki_page_in_project(
                path=r.get("path") or "",
                page_source_ids=r.get("source_ids"),
                project_id=project_id,
                allowed_source_ids=allowed,
            )
        ][:limit]
    return WikiFlagsResponse(pages=[WikiFlagItem(**r) for r in rows])


@router.post("/lint/run")
def run_lint_endpoint(body: WikiLintRunRequest | None = None) -> dict:
    """W4 ops: scan pages for stale/conflict/orphan/broken and persist flags."""
    _require_wiki()
    from ..services.wiki.lint import run_lint_pass

    req = body or WikiLintRunRequest()
    return run_lint_pass(limit=req.limit, detect_orphan=req.detect_orphan)


@router.post("/lint/sweep")
def sweep_lint_endpoint(body: WikiLintSweepRequest | None = None) -> dict:
    """S1: re-lint currently flagged pages so obsolete lint flags are cleared."""
    _require_wiki()
    from ..services.wiki.lint import sweep_flagged_pages

    req = body or WikiLintSweepRequest()
    return sweep_flagged_pages(limit=req.limit, detect_orphan=req.detect_orphan)


@router.post("/lint/apply-broken")
def apply_broken_endpoint(body: WikiApplyBrokenRequest) -> dict:
    """S5: explicit Hub click — rewrite broken [[wikilink]] or append ## Related."""
    _require_wiki()
    from ..services.wiki.lint import apply_broken_fix

    try:
        return apply_broken_fix(
            path=body.path,
            broken=body.broken,
            replacement_path=body.replacement_path,
            mode=body.mode,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/graph")
def wiki_page_graph(
    limit: int = Query(default=500, ge=1, le=2000),
    kinds: str | None = Query(
        default=None,
        description="Comma-separated page kinds (e.g. material,concept,theme)",
    ),
    include_orphan: bool = Query(default=True),
    project_id: str | None = Query(default=None),
) -> dict:
    """Wiki page [[wikilink]] graph for Hub canvas (not materials/formulation KG)."""
    _require_wiki()
    from ..services.wiki.page_graph import build_page_graph, page_graph_enabled

    if not page_graph_enabled():
        raise HTTPException(
            status_code=409,
            detail="wiki_page_graph_enabled is false（FORMUMIND_WIKI_PAGE_GRAPH_ENABLED）",
        )
    kind_list = [k.strip() for k in (kinds or "").split(",") if k.strip()] or None
    try:
        return build_page_graph(
            limit=limit,
            kinds=kind_list,
            include_orphan=include_orphan,
            project_id=project_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class WikiSearchHit(BaseModel):
    id: str = ""
    path: str
    kind: str = ""
    title: str = ""
    norm_key: str = ""
    snippet: str = ""
    flags: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    rank: float = 0.0


class WikiSearchResponse(BaseModel):
    hits: list[WikiSearchHit]
    total: int
    mode: str = "fts"  # fts | fallback


@router.get("/search", response_model=WikiSearchResponse)
def search_pages(
    q: str = Query(min_length=1),
    kind: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    project_id: str | None = Query(default=None),
) -> WikiSearchResponse:
    """Phase 2: metadata + body search (FTS5 when enabled, else keyword fallback)."""
    _require_wiki()
    settings = get_settings()
    store = get_wiki_store()
    hits: list[WikiSearchHit] = []
    mode = "fallback"
    fetch_limit = min(200, max(limit * 5, limit)) if project_id else limit

    if getattr(settings, "wiki_fts_enabled", True):
        from ..services.wiki.fts import search_fts

        raw = search_fts(store._session_factory, q, kind=kind, limit=fetch_limit)
        if raw:
            mode = "fts"
            for r in raw:
                row = store.get_by_path(r["path"])
                hits.append(
                    WikiSearchHit(
                        id=row.id if row else "",
                        path=r["path"],
                        kind=r.get("kind") or (row.kind if row else ""),
                        title=r.get("title") or (row.title if row else ""),
                        norm_key=r.get("norm_key") or (row.norm_key if row else ""),
                        snippet=r.get("snippet") or "",
                        flags=list(row.flags or []) if row else [],
                        source_ids=list(row.source_ids or []) if row else [],
                        rank=float(r.get("rank") or 0),
                    )
                )

    if not hits:
        # Keyword fallback over listed pages (title/path/norm_key/body)
        from ..services.wiki.schema import parse_front_matter

        q_lower = q.lower()
        tokens = [t for t in q_lower.replace("/", " ").split() if t]
        if not tokens:
            tokens = [q_lower]
        scored: list[tuple[int, WikiSearchHit]] = []
        for row in store.list_pages(kind=kind, limit=min(500, max(50, fetch_limit * 10))):
            md = store.read_markdown(row.path) or ""
            _, body = parse_front_matter(md)
            blob = f"{row.title}\n{row.path}\n{row.norm_key}\n{body}".lower()
            score = sum(1 for t in tokens if t in blob)
            if score <= 0:
                continue
            snip = ""
            for t in tokens:
                idx = body.lower().find(t)
                if idx >= 0:
                    lo = max(0, idx - 40)
                    snip = body[lo : idx + 80].replace("\n", " ")
                    break
            scored.append(
                (
                    score,
                    WikiSearchHit(
                        id=row.id,
                        path=row.path,
                        kind=row.kind,
                        title=row.title or "",
                        norm_key=row.norm_key or "",
                        snippet=snip,
                        flags=list(row.flags or []),
                        source_ids=list(row.source_ids or []),
                        rank=-float(score),
                    ),
                )
            )
        scored.sort(key=lambda x: x[0], reverse=True)
        hits = [h for _, h in scored[:fetch_limit]]
        mode = "fallback"

    if project_id:
        from ..services.wiki.project_scope import project_source_id_set, wiki_page_in_project

        allowed = project_source_id_set(project_id)
        hits = [
            h
            for h in hits
            if wiki_page_in_project(
                path=h.path,
                page_source_ids=h.source_ids,
                project_id=project_id,
                allowed_source_ids=allowed,
            )
        ][:limit]

    return WikiSearchResponse(hits=hits, total=len(hits), mode=mode)


class ThemeCompileRequest(BaseModel):
    system_key: str | None = None
    topic: str | None = None
    use_llm: bool = True


class DossierEnsureRequest(BaseModel):
    project_id: str = Field(min_length=1)
    campaign_id: str | None = None
    vertical: str | None = None
    use_llm: bool = False


class DossierPatchRequest(BaseModel):
    project_id: str = Field(min_length=1)
    sections: list[str] | None = None
    campaign_id: str | None = None
    vertical: str | None = None
    use_llm: bool = False


class DossierRefreshRequest(BaseModel):
    project_id: str = Field(min_length=1)
    sections: list[str] | None = None
    campaign_id: str | None = None
    vertical: str | None = None
    use_llm: bool = False


class DossierReportRequest(BaseModel):
    project_id: str = Field(min_length=1)
    template: str = Field(min_length=1, description="briefing|feasibility|formula-compare|patent-memo|deck")
    campaign_id: str | None = None
    prompt: str = ""
    use_llm: bool = False
    ensure_dossier: bool = True
    persist: bool = True


class DossierReportExportRequest(BaseModel):
    project_id: str = Field(min_length=1)
    template: str = Field(min_length=1)
    format: str = Field(default="docx", description="md|docx|pdf|pptx")
    campaign_id: str | None = None
    prompt: str = ""
    use_llm: bool = False
    ensure_dossier: bool = True


@router.post("/themes/compile")
def compile_theme_endpoint(body: ThemeCompileRequest) -> dict:
    """Phase 2: compile L2 system-overview theme (flag-gated, default off)."""
    _require_wiki()
    from ..services.wiki.theme import compile_theme

    try:
        return compile_theme(
            system_key=body.system_key,
            topic=body.topic,
            use_llm=body.use_llm,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/dossier/ensure")
def ensure_dossier_endpoint(body: DossierEnsureRequest) -> dict:
    """P4: create/refresh project dossier skeleton + data.json (flag-gated)."""
    _require_wiki()
    from ..services.wiki.dossier import ensure_project_dossier

    try:
        return ensure_project_dossier(
            body.project_id,
            campaign_id=body.campaign_id,
            vertical=body.vertical,
            use_llm=body.use_llm,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/dossier/patch")
def patch_dossier_endpoint(body: DossierPatchRequest) -> dict:
    """P4.2: rebuild selected dossier sections from live pack (deterministic)."""
    _require_wiki()
    from ..services.wiki.dossier import patch_dossier_sections

    try:
        return patch_dossier_sections(
            body.project_id,
            body.sections,
            campaign_id=body.campaign_id,
            vertical=body.vertical,
            use_llm=body.use_llm,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/dossier/refresh")
def refresh_dossier_endpoint(body: DossierRefreshRequest) -> dict:
    """P4.2: ensure dossier exists then patch sections (default: all)."""
    _require_wiki()
    from ..services.wiki.dossier import refresh_dossier

    try:
        return refresh_dossier(
            body.project_id,
            campaign_id=body.campaign_id,
            sections=body.sections,
            vertical=body.vertical,
            use_llm=body.use_llm,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/reports/templates")
def list_report_templates_endpoint() -> dict:
    """P5: list available DossierPack-backed report templates."""
    _require_wiki()
    from ..services.wiki.report import list_report_templates
    from ..services.wiki.report_export import export_capabilities

    return {"templates": list_report_templates(), "export": export_capabilities()}


@router.post("/dossier/report")
def generate_dossier_report_endpoint(body: DossierReportRequest) -> dict:
    """P5: generate a draft report from DossierPack (flag-gated)."""
    _require_wiki()
    from ..services.wiki.report import generate_report

    try:
        return generate_report(
            body.project_id,
            body.template,
            campaign_id=body.campaign_id,
            prompt=body.prompt or "",
            use_llm=body.use_llm,
            ensure_dossier=body.ensure_dossier,
            persist=body.persist,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class StormReportRequest(BaseModel):
    project_id: str = Field(min_length=1)
    topic: str = ""
    max_sections: int | None = Field(default=None, ge=3, le=12)
    perspectives: list[str] = Field(default_factory=list)
    use_llm: bool = False
    ensure_dossier: bool = True
    persist: bool = True
    campaign_id: str | None = None


@router.post("/storm/report", status_code=202)
def start_storm_report_endpoint(body: StormReportRequest, request: Request):
    """STORM longform report (async). Flag-gated; does not mutate sync /dossier/report."""
    import json as _json

    from fastapi.responses import JSONResponse

    from ..middleware.api_auth import get_current_owner
    from ..worker.tasks import run_wiki_storm_report_task
    from ._dispatch import submit

    _require_wiki()
    settings = get_settings()
    if not getattr(settings, "wiki_project_dossier_enabled", False):
        raise HTTPException(status_code=409, detail="wiki_project_dossier_enabled is false")
    if not getattr(settings, "wiki_dossier_report_enabled", False):
        raise HTTPException(status_code=409, detail="wiki_dossier_report_enabled is false")
    if not getattr(settings, "wiki_storm_report_enabled", False):
        raise HTTPException(status_code=409, detail="wiki_storm_report_enabled is false")

    payload = {
        "project_id": body.project_id,
        "topic": body.topic or "",
        "max_sections": body.max_sections,
        "perspectives": list(body.perspectives or []) or None,
        "use_llm": bool(body.use_llm),
        "ensure_dossier": bool(body.ensure_dossier),
        "persist": bool(body.persist),
        "campaign_id": body.campaign_id,
    }
    resp = submit(
        run_wiki_storm_report_task,
        payload,
        "wiki_storm_report",
        owner_id=get_current_owner(request),
    )
    content = _json.loads(resp.body)
    content["disclaimer"] = "draft_not_claims"
    return JSONResponse(status_code=202, content=content)


@router.get("/storm/report/{project_id}")
def get_storm_report_endpoint(project_id: str) -> dict:
    """Read persisted STORM longform page for a project (if any)."""
    _require_wiki()
    from ..services.wiki.schema import project_report_path

    store = get_wiki_store()
    path = project_report_path(project_id, "storm")
    row = store.get_by_path(path)
    if row is None:
        raise HTTPException(status_code=404, detail="storm report not found")
    md = store.read_markdown(row.path) or ""
    return {
        "path": row.path,
        "title": row.title or "",
        "markdown": md,
        "flags": list(row.flags or []),
        "source_ids": list(row.source_ids or []),
        "disclaimer": "draft_not_claims",
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.post("/dossier/report/export")
def export_dossier_report_endpoint(body: DossierReportExportRequest):
    """P5.1: generate + export report as md/docx/pdf/pptx."""
    _require_wiki()
    from fastapi.responses import Response

    from ..services.wiki.report import export_report

    try:
        out = export_report(
            body.project_id,
            body.template,
            body.format,
            campaign_id=body.campaign_id,
            prompt=body.prompt or "",
            use_llm=body.use_llm,
            ensure_dossier=body.ensure_dossier,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    headers = {
        "Content-Disposition": f'attachment; filename="{out["filename"]}"',
        "X-FormuMind-Report-Path": str(out.get("path") or ""),
        "X-FormuMind-Disclaimer": str(out.get("disclaimer") or "draft_not_claims"),
    }
    return Response(content=out["bytes"], media_type=out["media_type"], headers=headers)


@router.get("/dossier/{project_id}")
def get_dossier_endpoint(project_id: str) -> dict:
    """Return dossier markdown + sidecar JSON if present."""
    _require_wiki()
    from ..services.wiki.dossier import get_dossier_page

    try:
        page = get_dossier_page(project_id)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if page is None:
        raise HTTPException(status_code=404, detail="project dossier not found")
    return page


@router.get("/dossier/{project_id}/pack")
def get_dossier_pack_endpoint(project_id: str, campaign_id: str | None = None) -> dict:
    """Report foundation: structured DossierPack for the project."""
    _require_wiki()
    from ..services.wiki.dossier import get_dossier_pack

    try:
        return get_dossier_pack(project_id, campaign_id=campaign_id)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class WikiDraftSaveRequest(BaseModel):
    project_id: str = Field(min_length=1)
    question: str = ""
    answer_markdown: str = Field(min_length=1)
    title: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    citations: list[dict] = Field(default_factory=list)
    origin: str = Field(default="chat", description="chat | deep_research")


@router.post("/drafts/save")
def save_wiki_draft(body: WikiDraftSaveRequest) -> dict:
    """S4: persist Chat/Deep Research answer as L2 ``queries/`` draft (flag-gated)."""
    _require_wiki()
    from ..services.wiki.draft_save import save_chat_draft

    try:
        return save_chat_draft(
            project_id=body.project_id,
            question=body.question or "",
            answer_markdown=body.answer_markdown,
            title=body.title,
            source_ids=list(body.source_ids or []),
            citations=list(body.citations or []),
            origin=body.origin or "chat",
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class WikiCatalogRebuildRequest(BaseModel):
    persist: bool = True
    limit: int = Field(default=500, ge=1, le=500)
    kinds: str | None = Field(
        default=None,
        description="Comma-separated kinds filter (e.g. material,system,theme)",
    )
    project_id: str | None = None


@router.get("/catalog")
def get_wiki_catalog(
    format: str = Query(default="json", description="json | md"),
    limit: int = Query(default=500, ge=1, le=500),
    kinds: str | None = Query(default=None, description="Comma-separated kinds"),
    project_id: str | None = Query(default=None),
):
    """S2: deterministic wiki catalog (App-maintained; not a second SSOT)."""
    _require_wiki()
    from fastapi.responses import Response

    from ..services.wiki.catalog import build_catalog

    kind_list = [k.strip() for k in (kinds or "").split(",") if k.strip()] or None
    try:
        out = build_catalog(limit=limit, kinds=kind_list, project_id=project_id)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    fmt = (format or "json").strip().lower()
    if fmt in {"md", "markdown", "text"}:
        return Response(
            content=out["markdown"],
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="catalog.md"',
                "X-FormuMind-Catalog-Entries": str(out["entry_count"]),
            },
        )
    return out


@router.post("/catalog/rebuild")
def rebuild_wiki_catalog(body: WikiCatalogRebuildRequest | None = None) -> dict:
    """S2: rebuild catalog.md from wiki_pages (disk write optional; never upserts wiki_pages)."""
    _require_wiki()
    from ..services.wiki.catalog import rebuild_catalog

    req = body or WikiCatalogRebuildRequest()
    kind_list = [k.strip() for k in (req.kinds or "").split(",") if k.strip()] or None
    try:
        return rebuild_catalog(
            persist=req.persist,
            limit=req.limit,
            kinds=kind_list,
            project_id=req.project_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/fts/rebuild")
def rebuild_fts_endpoint() -> dict:
    """Rebuild Wiki FTS index from all pages (admin / ops)."""
    _require_wiki()
    if not getattr(get_settings(), "wiki_fts_enabled", True):
        raise HTTPException(status_code=409, detail="wiki_fts_enabled is false")
    from ..services.wiki.fts import rebuild_all

    store = get_wiki_store()
    n = rebuild_all(store._session_factory, store)
    return {"ok": True, "indexed": n}


@router.post("/embed/rebuild")
def rebuild_embed_endpoint() -> dict:
    """Phase 3: re-embed all wiki page summaries into document_chunks."""
    _require_wiki()
    if not getattr(get_settings(), "wiki_embed_enabled", False):
        raise HTTPException(status_code=409, detail="wiki_embed_enabled is false")
    from ..services.wiki.embed import rebuild_all_wiki_embeds

    return rebuild_all_wiki_embeds()


class WikiReviewUpdate(BaseModel):
    path: str
    reviewed: bool | None = None
    human_override: str | None = None


@router.post("/pages/review")
def review_page_endpoint(body: WikiReviewUpdate) -> dict:
    """Q4 reserved contract: toggle reviewed / human_override (not a full editor)."""
    _require_wiki()
    if body.reviewed is None and body.human_override is None:
        raise HTTPException(status_code=400, detail="reviewed or human_override required")
    from ..services.wiki.review import apply_page_review

    try:
        return apply_page_review(
            body.path,
            reviewed=body.reviewed,
            human_override=body.human_override,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"wiki page not found: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

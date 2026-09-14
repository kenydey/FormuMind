"""LLM Wiki read APIs (W1)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
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
) -> WikiPagesResponse:
    _require_wiki()
    rows = get_wiki_store().list_pages(kind=kind, limit=limit, offset=offset)
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


class WikiFlagItem(BaseModel):
    id: str
    path: str
    kind: str
    title: str = ""
    flags: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class WikiFlagsResponse(BaseModel):
    pages: list[WikiFlagItem]


@router.get("/flags", response_model=WikiFlagsResponse)
def list_flagged(limit: int = Query(default=50, ge=1, le=200)) -> WikiFlagsResponse:
    _require_wiki()
    from ..services.wiki.lint import list_flagged_pages

    rows = list_flagged_pages(limit=limit)
    return WikiFlagsResponse(pages=[WikiFlagItem(**r) for r in rows])


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
) -> WikiSearchResponse:
    """Phase 2: metadata + body search (FTS5 when enabled, else keyword fallback)."""
    _require_wiki()
    settings = get_settings()
    store = get_wiki_store()
    hits: list[WikiSearchHit] = []
    mode = "fallback"

    if getattr(settings, "wiki_fts_enabled", True):
        from ..services.wiki.fts import search_fts

        raw = search_fts(store._session_factory, q, kind=kind, limit=limit)
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
        for row in store.list_pages(kind=kind, limit=min(500, max(50, limit * 10))):
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
        hits = [h for _, h in scored[:limit]]
        mode = "fallback"

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

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

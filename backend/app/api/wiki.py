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

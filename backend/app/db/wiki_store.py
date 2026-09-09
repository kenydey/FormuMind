"""SQLite + disk store for LLM Wiki pages (W1)."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from .models import WikiPage
from .session_utils import commit_session
from ..config import get_settings
from ..services.wiki.schema import content_hash, wiki_root_from_db_url

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WikiStore:
    def __init__(self, session_factory: sessionmaker[Session], *, root: Path | None = None) -> None:
        self._session_factory = session_factory
        self._root = root

    def root(self) -> Path:
        if self._root is not None:
            return self._root
        return wiki_root_from_db_url(get_settings().db_url)

    def _abs(self, rel_path: str) -> Path:
        root = self.root().resolve()
        path = (root / rel_path).resolve()
        if not str(path).startswith(str(root)):
            raise ValueError(f"wiki path escapes root: {rel_path}")
        return path

    def upsert_page(
        self,
        *,
        path: str,
        kind: str,
        title: str,
        norm_key: str,
        entity_id: str | None,
        markdown: str,
        source_ids: list[str] | None = None,
        flags: list[str] | None = None,
        replace_source_ids: bool = False,
    ) -> WikiPage:
        rel = path.replace("\\", "/").lstrip("/")
        body = markdown
        digest = content_hash(body)
        abs_path = self._abs(rel)
        abs_path.parent.mkdir(parents=True, exist_ok=True)

        with commit_session(self._session_factory) as session:
            row = (
                session.query(WikiPage)
                .filter(WikiPage.path == rel)
                .one_or_none()
            )
            if row is None:
                row = WikiPage(
                    id=str(uuid.uuid4()),
                    path=rel,
                    kind=kind,
                    title=title[:512],
                    norm_key=(norm_key or "")[:200],
                    entity_id=(entity_id or None),
                    content_hash=digest,
                    source_ids=list(source_ids or []),
                    flags=list(flags or []),
                    revision=1,
                    created_at=_utcnow(),
                    updated_at=_utcnow(),
                )
                session.add(row)
                abs_path.write_text(body, encoding="utf-8")
                session.flush()
                return row

            incoming = list(source_ids or [])
            if replace_source_ids:
                merged_sources = incoming
            else:
                merged_sources = list(dict.fromkeys([*(row.source_ids or []), *incoming]))

            if row.content_hash == digest:
                # Still refresh source_ids / flags when asked
                changed = False
                if merged_sources != list(row.source_ids or []):
                    row.source_ids = merged_sources
                    changed = True
                if flags is not None and list(flags) != list(row.flags or []):
                    row.flags = list(flags)
                    changed = True
                if changed:
                    row.updated_at = _utcnow()
                return row

            row.title = title[:512]
            row.kind = kind
            row.norm_key = (norm_key or "")[:200]
            row.entity_id = entity_id or row.entity_id
            row.content_hash = digest
            row.source_ids = merged_sources
            if flags is not None:
                row.flags = list(flags)
            row.revision = int(row.revision or 1) + 1
            row.updated_at = _utcnow()
            abs_path.write_text(body, encoding="utf-8")
            session.flush()
            return row

    def get(self, page_id: str) -> WikiPage | None:
        with self._session_factory() as session:
            return session.get(WikiPage, page_id)

    def get_by_path(self, path: str) -> WikiPage | None:
        rel = path.replace("\\", "/").lstrip("/")
        with self._session_factory() as session:
            return (
                session.query(WikiPage)
                .filter(WikiPage.path == rel)
                .one_or_none()
            )

    def list_pages(
        self, *, kind: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[WikiPage]:
        with self._session_factory() as session:
            q = session.query(WikiPage).order_by(WikiPage.updated_at.desc())
            if kind:
                q = q.filter(WikiPage.kind == kind)
            return q.offset(max(0, offset)).limit(min(500, max(1, limit))).all()

    def read_markdown(self, path: str) -> str | None:
        try:
            p = self._abs(path)
        except ValueError:
            return None
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8")


_store: WikiStore | None = None


def get_wiki_store() -> WikiStore:
    global _store
    if _store is None:
        from .database import default_session_factory

        _store = WikiStore(default_session_factory())
    return _store

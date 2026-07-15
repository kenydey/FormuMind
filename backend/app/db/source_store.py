"""SQLite-backed source document store."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session, sessionmaker

from ..domain.schemas import SourceGuideSchema
from .models import SourceDocument
from .session_utils import commit_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SourceStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(
        self,
        *,
        filename: str,
        title: str,
        source_kind: str,
        full_text: str,
        content_hash: str,
        source_guide: SourceGuideSchema | None = None,
        extraction_status: str = "skipped",
        extraction_error: str | None = None,
        origin_url: str | None = None,
        project_id: str | None = None,
    ) -> str:
        source_id = str(uuid.uuid4())
        guide_payload = source_guide.model_dump(mode="json") if source_guide else None
        row = SourceDocument(
            id=source_id,
            filename=filename,
            title=title,
            source_kind=source_kind,
            content_hash=content_hash,
            origin_url=(origin_url or None),
            project_id=(project_id or None),
            full_text=full_text,
            raw_text_chars=len(full_text),
            source_guide=guide_payload,
            extraction_status=extraction_status,
            extraction_error=extraction_error,
            created_at=_utcnow(),
        )
        with commit_session(self._session_factory) as session:
            session.add(row)
        return source_id

    def get(self, source_id: str) -> SourceDocument | None:
        with self._session_factory() as session:
            return session.get(SourceDocument, source_id)

    def find_by_hash(self, content_hash: str) -> SourceDocument | None:
        with self._session_factory() as session:
            return (
                session.query(SourceDocument)
                .filter(SourceDocument.content_hash == content_hash)
                .order_by(SourceDocument.created_at.desc())
                .first()
            )

    def find_by_origin_url(self, origin_url: str) -> SourceDocument | None:
        """Async-ingest dedup: has this URL / patent id / DOI been acquired?"""
        key = (origin_url or "").strip()
        if not key:
            return None
        with self._session_factory() as session:
            return (
                session.query(SourceDocument)
                .filter(SourceDocument.origin_url == key)
                .order_by(SourceDocument.created_at.desc())
                .first()
            )

    def get_source_guide(self, source_id: str) -> SourceGuideSchema | None:
        row = self.get(source_id)
        if row is None or not row.source_guide:
            return None
        return SourceGuideSchema.model_validate(row.source_guide)

    def list_for_project(self, project_id: str | None, *, limit: int = 100) -> list[SourceDocument]:
        with self._session_factory() as session:
            q = session.query(SourceDocument).order_by(SourceDocument.created_at.desc())
            if project_id:
                q = q.filter(
                    (SourceDocument.project_id == project_id)
                    | (SourceDocument.project_id.is_(None))
                )
            return q.limit(limit).all()


_store: SourceStore | None = None


def get_source_store() -> SourceStore:
    global _store
    if _store is None:
        from .database import default_session_factory

        _store = SourceStore(default_session_factory())
    return _store

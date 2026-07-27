"""SQLite/Postgres-backed persistent chunk store for the knowledge base."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session, sessionmaker

from .models import DocumentChunk
from .session_utils import commit_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ChunkStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        # Bumped on every write; lets services cache derived indexes safely.
        self.generation = 0

    def replace_for_source_in(
        self, session: Session, source_id: str, chunks: list[dict]
    ) -> int:
        """``replace_for_source`` on a caller-owned session, without committing.

        Used by ``services/ingest_tx.ingest_document_tx`` so the chunk write
        joins the caller's transaction and rolls back with it. The INSERTs are
        flushed before returning so a unique-constraint race surfaces as
        ``IntegrityError`` here rather than at the caller's commit.

        Each chunk dict: {text, heading_path?, page_no?, paragraph_idx?,
        offset_start?, offset_end?, meta?, embedding?, embedding_model?}.

        offset_start/offset_end/paragraph_idx are persisted both as column-level
        values and inside ``meta`` (for back-compat until all consumers migrate).
        """
        session.query(DocumentChunk).filter(
            DocumentChunk.source_id == source_id
        ).delete()
        for i, chunk in enumerate(chunks):
            # Merge paragraph/offset provenance into meta dict — but only
            # when meta already carries extraction data, so that chunks
            # without chemical extraction keep meta=None (callers can tell
            # no extraction ran). Provenance is always available as
            # column-level values (paragraph_idx / offset_start / offset_end).
            meta = dict(chunk.get("meta") or {})
            if meta:
                for key in ("paragraph_idx", "offset_start", "offset_end"):
                    val = chunk.get(key)
                    if val is not None:
                        meta[key] = val
            if not meta:
                meta = None
            session.add(
                DocumentChunk(
                    id=str(uuid.uuid4()),
                    source_id=source_id,
                    ord=i,
                    text=chunk.get("text", ""),
                    heading_path=(chunk.get("heading_path") or "")[:120],
                    page_no=chunk.get("page_no"),
                    offset_start=chunk.get("offset_start"),
                    offset_end=chunk.get("offset_end"),
                    paragraph_idx=chunk.get("paragraph_idx"),
                    meta=meta,
                    embedding=chunk.get("embedding"),
                    embedding_model=chunk.get("embedding_model"),
                    created_at=_utcnow(),
                )
            )
        session.flush()
        return len(chunks)

    def bump_generation(self) -> None:
        """Invalidate derived caches. Callers of ``replace_for_source_in`` must
        call this *after* their transaction commits — bumping inside the write
        would invalidate caches for a transaction that may still roll back."""
        self.generation += 1

    def replace_for_source(self, source_id: str, chunks: list[dict]) -> int:
        """Idempotently (re)write the chunk rows of one source document.

        Self-contained variant: opens its own session, commits, and bumps the
        generation. See ``replace_for_source_in`` for the transactional-ingest
        variant where the caller owns both.
        """
        with commit_session(self._session_factory) as session:
            written = self.replace_for_source_in(session, source_id, chunks)
        self.bump_generation()
        return written

    def get_by_source(self, source_id: str) -> list[DocumentChunk]:
        # Returned ORM objects are detached (session closed); attribute access
        # works because expire_on_commit=False keeps values loaded. Callers
        # must not trigger lazy loads.
        with self._session_factory() as session:
            return (
                session.query(DocumentChunk)
                .filter(DocumentChunk.source_id == source_id)
                .order_by(DocumentChunk.ord)
                .all()
            )

    def all_chunks(self, limit: int | None = None, project_id: str | None = None) -> list[DocumentChunk]:
        # Returned ORM objects are detached (session closed); see get_by_source.
        with self._session_factory() as session:
            q = session.query(DocumentChunk).order_by(
                DocumentChunk.created_at.desc(), DocumentChunk.ord
            )
            if project_id:
                from .models import SourceDocument

                q = (
                    q.join(SourceDocument, DocumentChunk.source_id == SourceDocument.id)
                    .filter(
                        (SourceDocument.project_id == project_id)
                        | (SourceDocument.project_id.is_(None))
                    )
                )
            if limit:
                q = q.limit(limit)
            return q.all()

    def counts(self) -> tuple[int, int]:
        """(total chunks, chunks with embeddings)."""
        with self._session_factory() as session:
            total = session.query(func.count(DocumentChunk.id)).scalar() or 0
            embedded = (
                session.query(func.count(DocumentChunk.id))
                .filter(DocumentChunk.embedding.isnot(None))
                .scalar()
                or 0
            )
            return int(total), int(embedded)

    def delete_for_source(self, source_id: str) -> int:
        with commit_session(self._session_factory) as session:
            n = (
                session.query(DocumentChunk)
                .filter(DocumentChunk.source_id == source_id)
                .delete()
            )
        self.generation += 1
        return int(n)


_store: ChunkStore | None = None


def get_chunk_store() -> ChunkStore:
    global _store
    if _store is None:
        from .database import default_session_factory

        _store = ChunkStore(default_session_factory())
    return _store

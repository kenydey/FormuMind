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

    def replace_for_source(self, source_id: str, chunks: list[dict]) -> int:
        """Idempotently (re)write the chunk rows of one source document.

        Each chunk dict: {text, heading_path?, embedding?, embedding_model?}.
        """
        with commit_session(self._session_factory) as session:
            session.query(DocumentChunk).filter(
                DocumentChunk.source_id == source_id
            ).delete()
            for i, chunk in enumerate(chunks):
                session.add(
                    DocumentChunk(
                        id=str(uuid.uuid4()),
                        source_id=source_id,
                        ord=i,
                        text=chunk.get("text", ""),
                        heading_path=(chunk.get("heading_path") or "")[:120],
                        embedding=chunk.get("embedding"),
                        embedding_model=chunk.get("embedding_model"),
                        created_at=_utcnow(),
                    )
                )
        self.generation += 1
        return len(chunks)

    def get_by_source(self, source_id: str) -> list[DocumentChunk]:
        with self._session_factory() as session:
            return (
                session.query(DocumentChunk)
                .filter(DocumentChunk.source_id == source_id)
                .order_by(DocumentChunk.ord)
                .all()
            )

    def all_chunks(self, limit: int | None = None) -> list[DocumentChunk]:
        with self._session_factory() as session:
            q = session.query(DocumentChunk).order_by(
                DocumentChunk.created_at.desc(), DocumentChunk.ord
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

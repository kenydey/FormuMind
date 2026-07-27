"""Single-transaction document ingest — atomic SourceDocument + chunks + outbox.

Task 3.3: Replace the three-transaction pattern in POST /api/kb/ingest with
one session, one commit.  Idempotency is enforced by the unique constraint
on document_chunks(source_id, ord) (migration 0010).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker, Session

logger = logging.getLogger(__name__)


@dataclass
class IngestTxResult:
    source_id: str
    chunk_count: int       # 0 = idempotent hit
    already_existed: bool


def ingest_document_tx(
    session_factory: sessionmaker[Session],
    *,
    source_id: str,
    text: str,
    title: str = "",
    metadata: dict | None = None,
) -> IngestTxResult:
    """Atomic full-document ingest: SourceDocument + chunks + outbox, one commit.

    Idempotent:  Repeating the same ``source_id`` returns ``chunk_count=0``
    and ``already_existed=True``.  Concurrent double-submit is resolved by the
    ``uq_document_chunks_source_ord`` unique constraint — the loser catches
    the IntegrityError and returns the idempotent response.

    Any failure during the transaction rolls back everything: no orphan
    SourceDocument rows, no stale chunks, no dangling outbox records.
    """
    from ..config import get_settings
    from ..db.chunk_store import get_chunk_store
    from ..db.models import DocumentChunk, SourceDocument
    from ..db.outbox_store import enqueue
    from .chunking import chunk_markdown
    from .kb_index import _embed_model_name, _embed_texts, _embedding_probe, kb_enabled

    if not kb_enabled() or not (text or "").strip():
        return IngestTxResult(source_id=source_id, chunk_count=0, already_existed=False)

    chunk_store = get_chunk_store()
    settings = get_settings()

    with session_factory() as session:
        try:
            # ── 1. SourceDocument savepoint-upsert ──────────────────────────
            doc = session.get(SourceDocument, source_id)
            if doc is None:
                sp = session.begin_nested()
                try:
                    session.add(
                        SourceDocument(
                            id=source_id,
                            filename=title or "api_ingest",
                            title=title or "API Ingest",
                            source_kind="api",
                            full_text=text,
                            content_hash=hashlib.sha256(
                                text.encode("utf-8", errors="replace")
                            ).hexdigest(),
                            raw_text_chars=len(text),
                        )
                    )
                    session.flush()
                except IntegrityError:
                    sp.rollback()
                    doc = session.get(SourceDocument, source_id)
                    if doc is None:
                        raise
                # Sp not committed — caller's rollback can undo this INSERT.
                # See formumind-dev skill §19 for why.

            # ── 2. Chunk idempotency check + write ─────────────────────────
            existing = session.query(DocumentChunk).filter(
                DocumentChunk.source_id == source_id
            ).first()
            if existing is not None:
                return IngestTxResult(
                    source_id=source_id, chunk_count=0, already_existed=True
                )

            # Chunk the text
            chunks = chunk_markdown(
                text,
                max_chars=settings.ingest_chunk_max_chars,
                overlap=settings.ingest_chunk_overlap,
            )
            chunks = [
                c for c in chunks if len(c.text.strip()) > 30
            ][: settings.kb_max_chunks_per_source]

            rows: list[dict] = [
                {
                    "text": c.text,
                    "heading_path": c.heading_path,
                    "page_no": c.page_no,
                    "paragraph_idx": c.paragraph_idx,
                    "offset_start": c.offset_start,
                    "offset_end": c.offset_end,
                }
                for c in chunks
            ]

            # Embed if available
            if rows and _embedding_probe():
                vectors = _embed_texts([r["text"] for r in rows])
                if vectors:
                    model_name = _embed_model_name()
                    for row, vec in zip(rows, vectors):
                        row["embedding"] = vec
                        row["embedding_model"] = model_name

            # Write chunks via the caller-session method (no internal commit)
            try:
                chunk_count = chunk_store.replace_for_source_in(session, source_id, rows)
            except IntegrityError:
                # Concurrent insert hit the unique constraint —
                # another request wrote chunks between our SELECT check
                # and INSERT.  Verify and return idempotent.
                session.rollback()
                with session_factory() as verify_s:
                    verify_row = verify_s.query(DocumentChunk).filter(
                        DocumentChunk.source_id == source_id
                    ).first()
                    if verify_row is not None:
                        return IngestTxResult(
                            source_id=source_id, chunk_count=0, already_existed=True
                        )
                raise

            # ── 3. Outbox enqueue (idempotent, savepoint-guarded internally)─
            enqueue(
                session,
                operation="ingest_complete",
                idempotency_key=source_id,
                payload={
                    "source_id": source_id,
                    "chunk_count": chunk_count,
                    "status": "ok",
                },
            )

            # ── 4. Single commit — everything or nothing ──────────────────
            session.commit()
            chunk_store.bump_generation()

            return IngestTxResult(
                source_id=source_id, chunk_count=chunk_count, already_existed=False
            )

        except Exception:
            session.rollback()
            raise

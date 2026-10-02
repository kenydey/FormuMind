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
    from ..db.session_utils import commit_session
    from .chunking import chunk_markdown
    from .kb_index import _embed_model_name, _embed_texts, _embedding_probe, kb_enabled

    if not kb_enabled() or not (text or "").strip():
        return IngestTxResult(source_id=source_id, chunk_count=0, already_existed=False)

    chunk_store = get_chunk_store()
    settings = get_settings()

    with commit_session(session_factory) as session:
        try:
            # ── 1. SourceDocument upsert ──────────────────────────────────
            doc = session.get(SourceDocument, source_id)
            if doc is None:
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
                # No savepoint (see outbox_store.enqueue): begin_nested() on
                # SQLite commits the flushed INSERT, so a caller rollback could
                # not undo it. A concurrent-duplicate IntegrityError propagates;
                # the retry sees the now-committed document (idempotent).
                session.flush()

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

            # v7 KB-2: 补化学实体提取（与 index_source 同口径，否则 meta.chem 缺失）。
            from .kb_index import _attach_entities, _detect_chunk_lang

            for _r in rows:
                _r["lang"] = _detect_chunk_lang(_r.get("text") or "")
            _attach_entities(source_id, rows)

            # Embed if available
            # v7 KB-3: 双语分流（与 index_source 同口径）——zh 用 bge 512d，
            # 其余用 MiniLM 384d；此前统一用默认模型导致中文向量错配。
            if rows and _embedding_probe():
                from .rag import embed_model_name as _model_for_lang

                def _lang_of(r: dict) -> str:
                    return r.get("lang") or "en"

                group_idxs: dict[str, list[int]] = {}
                for _i, _r in enumerate(rows):
                    group_idxs.setdefault(_lang_of(_r), []).append(_i)
                vec_map: dict[int, list[float]] = {}
                model_per_row: dict[int, str] = {}
                mismatch = False
                for lang, idxs in group_idxs.items():
                    mname = _model_for_lang(lang)
                    texts = [rows[i]["text"] for i in idxs]
                    vecs = _embed_texts(texts, mname)
                    if not vecs or len(vecs) != len(idxs):
                        mismatch = True
                        break
                    for j, i in enumerate(idxs):
                        vec_map[i] = vecs[j]
                        model_per_row[id(rows[i])] = mname
                vectors = None
                if not mismatch and len(vec_map) == len(rows):
                    vectors = [vec_map[i] for i in range(len(rows))]
                else:
                    logger.error(
                        "kb embedding count mismatch for source %s — skipping embeddings",
                        source_id,
                    )
                if vectors:
                    for i, row in enumerate(rows):
                        row["embedding"] = vectors[i]
                        row["embedding_model"] = model_per_row.get(id(row)) or _embed_model_name()

            # KB dedup (2026-10-01): drop exact / near-duplicate chunks before
            # the write; the caller-owned session is reused read-only.
            from .kb_dedup import dedupe_chunk_rows

            rows = dedupe_chunk_rows(rows, source_id, session)
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

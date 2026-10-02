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
    failed: bool = False   # v9: 0-chunk / 全过滤时为 True（P0-3 修复裸 dict 崩溃）


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
    from .kb_retrieval_gate import gate_ingest_rows, ingest_block_reason_for_source

    if not kb_enabled() or not (text or "").strip():
        return IngestTxResult(source_id=source_id, chunk_count=0, already_existed=False)

    chunk_store = get_chunk_store()
    settings = get_settings()

    with commit_session(session_factory) as session:
        # v10: blocked origin 永不写 chunks —— 与 index_source 同口径，
        # 入口即拦截并回滚（不留 SourceDocument 僵尸行）。
        if ingest_block_reason_for_source(source_id):
            session.rollback()
            return IngestTxResult(
                source_id=source_id, chunk_count=0, already_existed=False, failed=True
            )
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
                        # v8: 与 fulltext_fetcher 同口径，避免 NULL 漏算
                        ingest_status="indexed",
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
            # U-1: 公共 chunk 准备（chunking → lang → entity → embedding → dedupe）
            # 与 index_source 共用，保证双写入路径永远一致。
            # session 透传给 helper（dedupe 用 caller session 做 DB 比对）。
            from .kb_index import prepare_chunk_rows

            # v10: 接入质量门 —— 与 index_source 同口径（gate 在 embedding/dedupe
            # 之前）；全过滤时 prepare 返回 None，走 failed+rollback 分支。
            rows = prepare_chunk_rows(
                text,
                source_id,
                embed=_embedding_probe(),
                session=session,
                gate_fn=lambda r: gate_ingest_rows(r, source_id=source_id),
            )
            # v8: 0-chunk 不提交空 SourceDocument —— 标记 failed，避免僵尸行
            #（与 v7 KB-1 的上传路径同口径）。
            if not rows:
                session.rollback()
                return IngestTxResult(
                    source_id=source_id, chunk_count=0, already_existed=False, failed=True
                )
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
            # Audit record of a unit of work that committed in this very
            # transaction: born DONE, never PENDING — nothing is left to
            # dispatch, and a PENDING row would only be "recovered" (and
            # eventually marked DEAD) by every restart.
            enqueue(
                session,
                operation="ingest_complete",
                idempotency_key=source_id,
                payload={
                    "source_id": source_id,
                    "chunk_count": chunk_count,
                    "status": "ok",
                },
                status="DONE",
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

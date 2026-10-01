"""SQLite/Postgres-backed persistent chunk store for the knowledge base."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, sessionmaker

from .models import DocumentChunk
from .db_common import validate_bbox
from .session_utils import commit_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _detect_chunk_lang(text: str) -> str | None:
    """轻量语言判定(双语分流, 2026-09-04): CJK 占比 ≥25% → zh, 否则 en。

    与 scripts/backfill_chunk_lang.py 阈值一致; 乱码检测由回填脚本负责
    (存量), 增量这里只分 zh/en(乱码按 en 处理, 由清洗工单收尾)。
    """
    if not text:
        return None
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    if cjk / max(1, len(text)) >= 0.25:
        return "zh"
    return "en"


class ChunkStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        # Bumped on every write; lets services cache derived indexes safely.
        self.generation = 0

    @property
    def session_factory(self) -> sessionmaker[Session]:
        """The factory this store was built with (lets services open
        read sessions against the same database the store writes)."""
        return self._session_factory

    def replace_for_source_in(
        self, session: Session, source_id: str, chunks: list[dict]
    ) -> int:
        """``replace_for_source`` on a caller-owned session, without committing.

        Used by ``services/ingest_tx.ingest_document_tx`` so the chunk write
        joins the caller's transaction and rolls back with it. The INSERTs are
        flushed before returning so a unique-constraint race surfaces as
        ``IntegrityError`` here rather than at the caller's commit.

        Each chunk dict: {text, heading_path?, page_no?, paragraph_idx?,
        offset_start?, offset_end?, meta?, embedding?, embedding_model?,
        bbox?, block_type?}.

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
            text = chunk.get("text", "") or ""
            # Phase 0 layout provenance: validate bbox at the write boundary so
            # a malformed box never lands in the table silently.
            raw_bbox = chunk.get("bbox")
            bbox = validate_bbox(raw_bbox) if raw_bbox is not None else None
            # C-1b: dual-write the vector as float32 BLOB alongside the legacy
            # JSON column (old readers still read JSON; faiss prefers BLOB).
            emb_list = chunk.get("embedding")
            emb_blob = None
            if emb_list:
                try:
                    from ..services.kb_ann import vector_to_blob

                    emb_blob = vector_to_blob(list(emb_list))
                except Exception:  # noqa: BLE001
                    emb_blob = None
            session.add(
                DocumentChunk(
                    id=str(uuid.uuid4()),
                    source_id=source_id,
                    ord=i,
                    text=text,
                    heading_path=(chunk.get("heading_path") or "")[:120],
                    page_no=chunk.get("page_no"),
                    bbox=bbox,
                    block_type=chunk.get("block_type") or "text",
                    offset_start=chunk.get("offset_start"),
                    offset_end=chunk.get("offset_end"),
                    paragraph_idx=chunk.get("paragraph_idx"),
                    meta=meta,
                    embedding=emb_list,
                    embedding_blob=emb_blob,
                    embedding_model=chunk.get("embedding_model"),
                    # 2026-09-04 (双语分流): 写入时自动标语言子库(显式
                    # chunk["lang"] 优先), 增量 ingest 无需跑回填脚本。
                    lang=chunk.get("lang") or _detect_chunk_lang(text),
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

    def get_by_source(
        self, source_id: str, *, limit: int | None = None, offset: int = 0
    ) -> list[DocumentChunk]:
        # Returned ORM objects are detached (session closed); attribute access
        # works because expire_on_commit=False keeps values loaded. Callers
        # must not trigger lazy loads.
        # P-6: SQL-level pagination — previously .all() loaded every chunk.
        with self._session_factory() as session:
            q = (
                session.query(DocumentChunk)
                .filter(DocumentChunk.source_id == source_id)
                .order_by(DocumentChunk.ord)
                .offset(offset)
            )
            if limit is not None:
                q = q.limit(limit)
            return q.all()

    def all_chunks(
        self,
        limit: int | None = None,
        project_id: str | None = None,
        *,
        include_global: bool = False,
        include_archived: bool = False,
    ) -> list[DocumentChunk]:
        # Returned ORM objects are detached (session closed); see get_by_source.
        # Soft-archived sources (W3) are excluded from retrieval by default.
        # Use OUTER JOIN on the global path so orphan chunks (no SourceDocument
        # row — common in unit tests / partial fixtures) still participate.
        from sqlalchemy import or_

        from .models import SourceDocument

        with self._session_factory() as session:
            q = session.query(DocumentChunk).order_by(
                DocumentChunk.created_at.desc(), DocumentChunk.ord
            )
            if project_id:
                q = q.join(SourceDocument, DocumentChunk.source_id == SourceDocument.id)
                if include_global:
                    q = q.filter(
                        (SourceDocument.project_id == project_id)
                        | (SourceDocument.project_id.is_(None))
                    )
                else:
                    q = q.filter(SourceDocument.project_id == project_id)
                if not include_archived:
                    q = q.filter(
                        or_(
                            SourceDocument.archived.is_(False),
                            SourceDocument.archived.is_(None),
                        )
                    )
            elif not include_archived:
                q = q.outerjoin(
                    SourceDocument, DocumentChunk.source_id == SourceDocument.id
                )
                q = q.filter(
                    or_(
                        SourceDocument.id.is_(None),
                        SourceDocument.archived.is_(False),
                        SourceDocument.archived.is_(None),
                    )
                )
            if limit:
                q = q.limit(limit)
            return q.all()

    def embedded_fingerprint(self) -> dict[str, dict[str, object]]:
        """Per-model ``{count, max_created_at}`` over the indexed vector population.

        C-1 staleness guard: ``kb_ann`` records this in its manifest and
        rebuilds the faiss index whenever it changes. Insert, delete,
        replace and archive/unarchive all move ``count`` or
        ``max_created_at``. Same population filters as :meth:`embedded_vectors`.
        Fail-open: never raises; returns ``{}`` on DB error (the caller then
        treats the index as unverifiable and rebuilds).
        """
        from sqlalchemy import func, or_

        from .models import SourceDocument

        try:
            with self._session_factory() as session:
                rows = (
                    session.query(
                        DocumentChunk.embedding_model,
                        func.count(DocumentChunk.id),
                        func.max(DocumentChunk.created_at),
                    )
                    .outerjoin(
                        SourceDocument,
                        DocumentChunk.source_id == SourceDocument.id,
                    )
                    .filter(
                        or_(
                            SourceDocument.id.is_(None),
                            SourceDocument.archived.is_(False),
                            SourceDocument.archived.is_(None),
                        )
                    )
                    .filter(
                        or_(
                            DocumentChunk.embedding_blob.isnot(None),
                            DocumentChunk.embedding.isnot(None),
                        )
                    )
                    .group_by(DocumentChunk.embedding_model)
                    .all()
                )
        except Exception:  # noqa: BLE001
            return {}
        out: dict[str, dict[str, object]] = {}
        for model, count, max_created in rows:
            out[model or ""] = {
                "count": int(count),
                "max_created_at": str(max_created) if max_created else "",
            }
        return out

    def embedded_vectors(self) -> list[dict]:
        """Lightweight full-corpus vector scan for the C-1 faiss index build.

        Returns ``{"id", "embedding_blob", "embedding", "embedding_model"}``
        dicts for non-archived chunks that have a vector. Deliberately loads
        no text columns. Same archive visibility as :meth:`all_chunks`.
        """
        from sqlalchemy import or_

        from .models import SourceDocument

        with self._session_factory() as session:
            q = (
                session.query(
                    DocumentChunk.id,
                    DocumentChunk.embedding_blob,
                    DocumentChunk.embedding,
                    DocumentChunk.embedding_model,
                )
                .outerjoin(SourceDocument, DocumentChunk.source_id == SourceDocument.id)
                .filter(
                    or_(
                        SourceDocument.id.is_(None),
                        SourceDocument.archived.is_(False),
                        SourceDocument.archived.is_(None),
                    )
                )
                .filter(
                    or_(
                        DocumentChunk.embedding_blob.isnot(None),
                        DocumentChunk.embedding.isnot(None),
                    )
                )
            )
            return [
                {
                    "id": r[0],
                    "embedding_blob": r[1],
                    "embedding": r[2],
                    "embedding_model": r[3],
                }
                for r in q.all()
            ]

    def chunks_by_ids(
        self,
        ids: list[str],
        project_id: str | None = None,
        *,
        include_global: bool = False,
    ) -> list[DocumentChunk]:
        """Bulk-fetch chunks by id with the same project/archive visibility as
        :meth:`all_chunks` (P0 project isolation applies to faiss extras)."""
        if not ids:
            return []
        from sqlalchemy import or_

        from .models import SourceDocument

        with self._session_factory() as session:
            q = session.query(DocumentChunk).filter(DocumentChunk.id.in_(ids))
            if project_id:
                q = q.join(SourceDocument, DocumentChunk.source_id == SourceDocument.id)
                if include_global:
                    q = q.filter(
                        (SourceDocument.project_id == project_id)
                        | (SourceDocument.project_id.is_(None))
                    )
                else:
                    q = q.filter(SourceDocument.project_id == project_id)
                q = q.filter(
                    or_(
                        SourceDocument.archived.is_(False),
                        SourceDocument.archived.is_(None),
                    )
                )
            else:
                q = q.outerjoin(
                    SourceDocument, DocumentChunk.source_id == SourceDocument.id
                ).filter(
                    or_(
                        SourceDocument.id.is_(None),
                        SourceDocument.archived.is_(False),
                        SourceDocument.archived.is_(None),
                    )
                )
            return q.all()

    def counts(self) -> tuple[int, int]:
        """(total chunks, chunks with embeddings).

        P0-1 起向量以 ``embedding_blob`` (BLOB) 为主, JSON ``embedding`` 列
        可能全空 —— 只数 JSON 会误报覆盖率 0%。有任一即算有向量。
        """
        with self._session_factory() as session:
            total = session.query(func.count(DocumentChunk.id)).scalar() or 0
            embedded = (
                session.query(func.count(DocumentChunk.id))
                .filter(
                    or_(
                        DocumentChunk.embedding_blob.isnot(None),
                        DocumentChunk.embedding.isnot(None),
                    )
                )
                .scalar()
                or 0
            )
            return int(total), int(embedded)

    def counts_active_archived(self) -> tuple[int, int]:
        """(active_chunks, archived_chunks) via SourceDocument.archived.

        Orphan chunks (no source row) count as active — same as retrieval OUTER JOIN.
        """
        from .models import SourceDocument

        with self._session_factory() as session:
            total = session.query(func.count(DocumentChunk.id)).scalar() or 0
            archived = (
                session.query(func.count(DocumentChunk.id))
                .join(SourceDocument, DocumentChunk.source_id == SourceDocument.id)
                .filter(SourceDocument.archived.is_(True))
                .scalar()
                or 0
            )
            return int(total) - int(archived), int(archived)

    def count_foreign_model(self, model_name: str) -> int:
        """Embedded chunks produced by some *other* embedding model.

        These are the rows retrieval has to skip: their vectors live in a
        different semantic space, so scoring them against the current model
        would compare two unrelated things. Counting them is what lets the
        stats endpoint say so instead of reporting the corpus as fully
        semantic.

        NULL is not counted — those rows predate the column being populated and
        are judged on dimension alone (see ``kb_index.comparable_embedding``).

        向量载体可能是 ``embedding_blob`` (P0-1 起) 或旧 JSON ``embedding``,
        有任一即纳入统计, 否则换模型重回填后该计数恒为 0 而误报全量 fresh。
        """
        with self._session_factory() as session:
            return int(
                session.query(func.count(DocumentChunk.id))
                .filter(
                    or_(
                        DocumentChunk.embedding_blob.isnot(None),
                        DocumentChunk.embedding.isnot(None),
                    )
                )
                .filter(DocumentChunk.embedding_model.isnot(None))
                .filter(DocumentChunk.embedding_model != model_name)
                .scalar()
                or 0
            )

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

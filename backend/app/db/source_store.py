"""SQLite-backed source document store."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ..domain.schemas import SourceGuideSchema
from .models import SourceDocument
from .session_utils import commit_session

logger = logging.getLogger(__name__)


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
        acquisition: str | None = None,
        parser: str | None = None,  # P3-5: parser tier provenance
        ingest_status: str | None = None,  # P1-1: 'indexed' | 'failed' | None
        ingest_error: str | None = None,  # P1-1: last ingest failure reason
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
            acquisition=(acquisition or None),
            parser=(parser or None),
            ingest_status=(ingest_status or None),
            ingest_error=(ingest_error or None),
            archived=False,
            created_at=_utcnow(),
        )
        with commit_session(self._session_factory) as session:
            session.add(row)
        return source_id

    def count_for_project(self, project_id: str | None, *, acquisition: str | None = None) -> int:
        """Rows stored for a project, optionally filtered by acquisition path.

        Used by the per-project quotas: ``kb_project_source_quota`` counts every
        row, ``kb_project_pdf_quota`` counts only ``acquisition == "pdf"`` (the
        download+parse path that dominates memory). Archived rows still count —
        they occupy disk until hard-deleted.
        """
        if not project_id:
            return 0
        with self._session_factory() as session:
            q = session.query(SourceDocument).filter(SourceDocument.project_id == project_id)
            if acquisition is not None:
                q = q.filter(SourceDocument.acquisition == acquisition)
            return q.count()

    def set_archived(self, source_id: str, archived: bool = True) -> bool:
        """Soft-archive or restore a source. Returns False if the row is missing."""
        with commit_session(self._session_factory) as session:
            doc = session.get(SourceDocument, source_id)
            if doc is None:
                return False
            want = bool(archived)
            doc.archived = want
            if want:
                if getattr(doc, "archived_at", None) is None:
                    doc.archived_at = _utcnow()
            else:
                doc.archived_at = None
            return True

    def list_archived_expired(self, *, days: int, limit: int = 500) -> list[SourceDocument]:
        """Archived sources older than ``days`` (by archived_at, else created_at)."""
        from datetime import timedelta

        from sqlalchemy import and_, or_

        if days < 1:
            return []
        cutoff = _utcnow() - timedelta(days=int(days))
        with self._session_factory() as session:
            # Prefer archived_at; fall back to created_at for legacy rows.
            q = (
                session.query(SourceDocument)
                .filter(SourceDocument.archived.is_(True))
                .filter(
                    or_(
                        and_(
                            SourceDocument.archived_at.isnot(None),
                            SourceDocument.archived_at < cutoff,
                        ),
                        and_(
                            SourceDocument.archived_at.is_(None),
                            SourceDocument.created_at < cutoff,
                        ),
                    )
                )
                .order_by(SourceDocument.created_at.asc())
                .limit(limit)
            )
            return q.all()

    def count_archived_split(self) -> tuple[int, int]:
        """Return (active_sources, archived_sources)."""
        from sqlalchemy import func

        with self._session_factory() as session:
            total = session.query(func.count(SourceDocument.id)).scalar() or 0
            archived = (
                session.query(func.count(SourceDocument.id))
                .filter(SourceDocument.archived.is_(True))
                .scalar()
                or 0
            )
            return int(total) - int(archived), int(archived)

    def get(self, source_id: str) -> SourceDocument | None:
        with self._session_factory() as session:
            return session.get(SourceDocument, source_id)

    def find_by_hash(self, content_hash: str) -> SourceDocument | None:
        # P1-8 修正：忽略 ingest_status='failed' 的行。失败行保留真实
        # content_hash（供复活路径按 hash 校验），但 hash 命中必须只返回
        # 可用行，否则下一次 _persist_fulltext 直接命中零-chunk 失败行，
        # 形成"已索引"僵尸。失败行走 find_failed_by_origin 复活路径。
        with self._session_factory() as session:
            return (
                session.query(SourceDocument)
                .filter(SourceDocument.content_hash == content_hash)
                .filter(
                    (SourceDocument.ingest_status.is_(None))
                    | (SourceDocument.ingest_status != "failed")
                )
                .order_by(SourceDocument.created_at.desc())
                .first()
            )

    def find_by_origin_url(self, origin_url: str) -> SourceDocument | None:
        """Async-ingest dedup: has this URL / patent id / DOI been acquired?

        Tries patent-id aliases (compact / SCPN hyphen / Google Patents URL)
        so ``CN-104789083-B`` and ``CN104789083B`` hit the same row.
        """
        return self.find_by_origin_urls([origin_url] if origin_url else [])

    def find_by_origin_urls(self, origin_urls: list[str]) -> SourceDocument | None:
        """Dedup lookup across any of the given origin_url keys (and aliases)."""
        from ..services.patent_ids import patent_id_aliases

        keys: list[str] = []
        seen: set[str] = set()
        for raw in origin_urls or []:
            for a in patent_id_aliases(raw):
                if a not in seen:
                    seen.add(a)
                    keys.append(a)
            s = (raw or "").strip()
            if s and s not in seen:
                seen.add(s)
                keys.append(s[:1024])
        if not keys:
            return None
        with self._session_factory() as session:
            return (
                session.query(SourceDocument)
                .filter(SourceDocument.origin_url.in_(keys))
                .order_by(SourceDocument.created_at.desc())
                .first()
            )

    # ── P1-1: ingest failure observability ────────────────────────────────

    def record_ingest_failure(
        self,
        *,
        origin_url: str | None,
        filename: str,
        title: str,
        source_kind: str,
        project_id: str | None,
        error: str,
    ) -> str | None:
        """Persist a per-document ingest failure, keyed by origin URL.

        Upserts: an existing row for this origin gets ingest_status='failed' +
        the (truncated) error; otherwise a minimal failed row is inserted so
        the failure is queryable. Never raises — observability must not break
        ingest. Returns the row id, or None when there is no usable origin.
        """
        import hashlib

        origin = (origin_url or "").strip()[:1024] or None
        if not origin:
            return None
        err = (error or "未知失败")[:500]
        try:
            with commit_session(self._session_factory) as session:
                row = (
                    session.query(SourceDocument)
                    .filter(SourceDocument.origin_url == origin)
                    .order_by(SourceDocument.created_at.desc())
                    .first()
                )
                if row is not None:
                    # Only ever (re-)mark rows that are already failed: a real
                    # row (indexed now or legacy, possibly with full_text
                    # already cleared by clear_full_text) must never be
                    # downgraded by a later failed attempt for the same origin
                    # (tier-1 dedup skips indexed rows before we get here, but
                    # belt-and-suspenders for other callers).
                    if row.ingest_status == "failed" or row.extraction_status == "failed":
                        row.ingest_status = "failed"
                        row.ingest_error = err
                    return row.id
                row = SourceDocument(
                    id=str(uuid.uuid4()),
                    filename=filename[:500],
                    title=title[:500],
                    source_kind=source_kind[:32],
                    content_hash=hashlib.sha256(
                        f"ingest-failure:{origin}".encode("utf-8")
                    ).hexdigest(),
                    origin_url=origin,
                    project_id=(project_id or None),
                    full_text=None,
                    raw_text_chars=0,
                    extraction_status="failed",
                    ingest_status="failed",
                    ingest_error=err,
                    archived=False,
                    created_at=_utcnow(),
                )
                session.add(row)
                return row.id
        except Exception as exc:
            logger.warning("record_ingest_failure 落盘失败: %s", exc)
            return None

    def find_failed_by_origin(self, origin_url: str | None) -> SourceDocument | None:
        """Newest row for this origin whose last ingest attempt failed."""
        origin = (origin_url or "").strip()[:1024]
        if not origin:
            return None
        with self._session_factory() as session:
            return (
                session.query(SourceDocument)
                .filter(SourceDocument.origin_url == origin)
                .filter(SourceDocument.ingest_status == "failed")
                .order_by(SourceDocument.created_at.desc())
                .first()
            )

    def revive_failed_row(
        self,
        source_id: str,
        *,
        full_text: str,
        content_hash: str,
        filename: str | None = None,
        title: str | None = None,
    ) -> None:
        """Promote a previously-failed row to indexed in place.

        Keeps one row per origin URL: a retry that succeeds updates the failed
        row instead of inserting a duplicate.
        """
        with commit_session(self._session_factory) as session:
            row = session.get(SourceDocument, source_id)
            if row is None:
                return
            row.full_text = full_text
            row.raw_text_chars = len(full_text)
            row.content_hash = content_hash
            if filename:
                row.filename = filename[:500]
            if title:
                row.title = title[:500]
            row.extraction_status = "fulltext"
            row.ingest_status = "indexed"
            row.ingest_error = None

    def clear_full_text(self, source_id: str) -> None:
        """入库收尾：清空冗余的 full_text（切块已覆盖全文，检索不再读它）。

        只清空字段、保留行——content_hash 去重 / 左栏列表 / source_guide 都还依赖
        这一行存在。已为 NULL 则不动（幂等）。
        """
        with commit_session(self._session_factory) as session:
            doc = session.get(SourceDocument, source_id)
            if doc is not None and doc.full_text:
                doc.full_text = None

    def get_source_guide(self, source_id: str) -> SourceGuideSchema | None:
        row = self.get(source_id)
        if row is None or not row.source_guide:
            return None
        return SourceGuideSchema.model_validate(row.source_guide)

    def update_fields(self, source_id: str, **fields: Any) -> bool:
        """Guarded partial update of a SourceDocument row.

        P2-10 provenance write-protection: the reserved fields
        (``source_url``/``retrieved_at``/``content_hash`` and the model-level
        equivalents ``origin_url``/``created_at`` — see
        ``services.provenance.RESERVED_SOURCE_FIELDS``) record where a piece of
        evidence came from and must not be rewritten by agents or callers. An
        update request naming any of them is rejected with a warning.
        Only real ``SourceDocument`` columns are applied; unknown names raise
        ``ValueError``. Returns False when the row is missing.
        """
        from ..services.provenance import RESERVED_SOURCE_FIELDS

        bad = RESERVED_SOURCE_FIELDS.intersection(fields)
        if bad:
            logger.warning(
                "source_store.update_fields rejected reserved field(s) %s for %s",
                sorted(bad),
                source_id,
            )
            raise ValueError(
                f"reserved source fields are write-protected: {sorted(bad)}"
            )
        columns = set(SourceDocument.__table__.columns.keys())
        unknown = set(fields) - columns
        if unknown:
            raise ValueError(f"unknown SourceDocument field(s): {sorted(unknown)}")
        with commit_session(self._session_factory) as session:
            doc = session.get(SourceDocument, source_id)
            if doc is None:
                return False
            for name, value in fields.items():
                setattr(doc, name, value)
            return True

    def list_for_project(
        self,
        project_id: str | None,
        *,
        limit: int = 100,
        include_global: bool = False,
        include_archived: bool = False,
    ) -> list[SourceDocument]:
        """List sources for a project.

        When ``project_id`` is set and ``include_global`` is False (default),
        only rows stamped with that project are returned — Knowledge Hub /
        project-scoped browse must not leak other projects or the shared
        global corpus (``project_id IS NULL``).

        Soft-archived rows are hidden unless ``include_archived`` is True.
        """
        from sqlalchemy import or_

        with self._session_factory() as session:
            q = session.query(SourceDocument).order_by(SourceDocument.created_at.desc())
            if project_id:
                if include_global:
                    q = q.filter(
                        (SourceDocument.project_id == project_id)
                        | (SourceDocument.project_id.is_(None))
                    )
                else:
                    q = q.filter(SourceDocument.project_id == project_id)
            if not include_archived:
                q = q.filter(
                    or_(SourceDocument.archived.is_(False), SourceDocument.archived.is_(None))
                )
            return q.limit(limit).all()

    def delete(self, source_id: str) -> bool:
        """Hard-delete the SourceDocument row. Returns True if a row was removed."""
        with commit_session(self._session_factory) as session:
            doc = session.get(SourceDocument, source_id)
            if doc is None:
                return False
            session.delete(doc)
            return True


_store: SourceStore | None = None


def get_source_store() -> SourceStore:
    global _store
    if _store is None:
        from .database import default_session_factory

        _store = SourceStore(default_session_factory())
    return _store

"""Persistent store for KG entities, mentions, and links."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import Text, cast, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .models import KGEntity, KGEntityLink, KGMention
from .session_utils import commit_session

logger = logging.getLogger(__name__)

SEMANTIC_LINK_TYPES = frozenset(
    {
        "substitutes",
        "synergizes",
        "inhibits",
        "correlates_pos",
        "correlates_neg",
        "requires",
    }
)

STRUCTURAL_LINK_TYPES = frozenset(
    {
        "has_ingredient",
        "has_performance",
        "tested_on_substrate",
    }
)

EXTRACTED_LINK_TYPES = SEMANTIC_LINK_TYPES | STRUCTURAL_LINK_TYPES


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class EntityStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def delete_mentions_for_source(self, source_id: str) -> int:
        with commit_session(self._session_factory) as session:
            return int(
                session.query(KGMention)
                .filter(KGMention.source_id == source_id)
                .delete()
            )

    def delete_links_for_source(self, source_id: str) -> int:
        """Remove extracted links whose evidence cites *source_id* (preserve catalog_alias)."""
        removed = 0
        pattern = f"%{source_id}%"
        with commit_session(self._session_factory) as session:
            links = (
                session.query(KGEntityLink)
                .filter(KGEntityLink.link_type.in_(tuple(EXTRACTED_LINK_TYPES)))
                .filter(cast(KGEntityLink.evidence_refs, Text).like(pattern))
                .all()
            )
            for link in links:
                refs = list(link.evidence_refs or [])
                kept = [r for r in refs if r.get("source_id") != source_id]
                if len(kept) == len(refs):
                    continue
                if not kept:
                    session.delete(link)
                    removed += 1
                else:
                    link.evidence_refs = kept
                    link.updated_at = _utcnow()
            return removed

    def upsert_entity(self, session: Session, **fields) -> KGEntity:
        eid = fields["id"]

        def _merge(row: KGEntity) -> KGEntity:
            # Merge semantics: None values are skipped by design so callers
            # never accidentally clobber curated fields with blanks.
            for key, val in fields.items():
                if key == "id":
                    continue
                if val is None:
                    continue
                if key == "aliases" and val:
                    merged = list(dict.fromkeys((row.aliases or []) + list(val)))
                    row.aliases = merged
                elif key == "element_symbols" and val:
                    merged = list(dict.fromkeys((row.element_symbols or []) + list(val)))
                    row.element_symbols = merged
                else:
                    setattr(row, key, val)
            row.updated_at = _utcnow()
            return row

        row = session.get(KGEntity, eid)
        if row is not None:
            return _merge(row)

        new_row = KGEntity(id=eid, created_at=_utcnow(), **{
            k: v for k, v in fields.items() if k != "id"
        })
        # Two sessions can race to insert the same new entity id (e.g. two
        # documents mentioning the same CAS number ingested concurrently);
        # without this guard the loser's IntegrityError propagates out of
        # upsert_entity and aborts the whole document's KG extraction.
        sp = session.begin_nested()
        try:
            session.add(new_row)
            session.flush()
            sp.commit()
        except IntegrityError:
            sp.rollback()
            row = session.get(KGEntity, eid)
            if row is not None:
                return _merge(row)
            raise
        except OperationalError:
            raise
        return new_row

    def add_mention(
        self,
        session: Session,
        *,
        entity_id: str,
        source_id: str,
        chunk_id: str,
        surface_form: str = "",
        confidence: float = 1.0,
        extractor: str = "chem_extract",
        meta: dict | None = None,
    ) -> KGMention | None:
        surface = (surface_form or "")[:256]
        existing = (
            session.query(KGMention)
            .filter(
                KGMention.entity_id == entity_id,
                KGMention.chunk_id == chunk_id,
                KGMention.surface_form == surface,
            )
            .first()
        )
        if existing:
            return existing
        mention = KGMention(
            id=str(uuid.uuid4()),
            entity_id=entity_id,
            source_id=source_id,
            chunk_id=chunk_id,
            surface_form=surface,
            confidence=confidence,
            extractor=extractor,
            meta=meta,
            created_at=_utcnow(),
        )
        sp = session.begin_nested()
        try:
            session.add(mention)
            session.flush()
            sp.commit()
        except IntegrityError:
            sp.rollback()
            existing = (
                session.query(KGMention)
                .filter(
                    KGMention.entity_id == entity_id,
                    KGMention.chunk_id == chunk_id,
                    KGMention.surface_form == surface,
                )
                .first()
            )
            return existing
        except OperationalError:
            raise
        return mention

    def add_link(
        self,
        session: Session,
        *,
        src_entity_id: str,
        dst_entity_id: str,
        link_type: str,
        confidence: float,
        evidence_refs: list[dict] | None = None,
        extraction_method: str = "manual",
    ) -> None:
        dup = (
            session.query(KGEntityLink)
            .filter(
                KGEntityLink.src_entity_id == src_entity_id,
                KGEntityLink.dst_entity_id == dst_entity_id,
                KGEntityLink.link_type == link_type,
            )
            .first()
        )
        if dup:
            return
        sp = session.begin_nested()
        try:
            session.add(
                KGEntityLink(
                    id=str(uuid.uuid4()),
                    src_entity_id=src_entity_id,
                    dst_entity_id=dst_entity_id,
                    link_type=link_type,
                    confidence=confidence,
                    evidence_refs=evidence_refs or [],
                    extraction_method=extraction_method,
                    created_at=_utcnow(),
                    updated_at=_utcnow(),
                )
            )
            session.flush()
            sp.commit()
        except IntegrityError:
            sp.rollback()
        except OperationalError:
            raise

    def merge_semantic_link(
        self,
        session: Session,
        *,
        src_entity_id: str,
        dst_entity_id: str,
        link_type: str,
        confidence: float,
        evidence_ref: dict,
        metadata: dict | None = None,
        extraction_method: str = "rule",
    ) -> bool:
        if src_entity_id == dst_entity_id or link_type not in SEMANTIC_LINK_TYPES:
            return False

        def _merge(existing: KGEntityLink) -> bool:
            refs = list(existing.evidence_refs or [])
            key = (
                evidence_ref.get("source_id"),
                evidence_ref.get("chunk_id"),
                evidence_ref.get("sentence"),
            )
            if not any(
                (r.get("source_id"), r.get("chunk_id"), r.get("sentence")) == key for r in refs
            ):
                refs.append(evidence_ref)
            existing.evidence_refs = refs[:20]
            existing.confidence = max(float(existing.confidence or 0), confidence)
            existing.extraction_method = extraction_method
            existing.is_valid = True
            existing.updated_at = _utcnow()
            if metadata:
                merged = dict(existing.metadata_json or {})
                merged.update(metadata)
                existing.metadata_json = merged
            return True

        existing = (
            session.query(KGEntityLink)
            .filter(
                KGEntityLink.src_entity_id == src_entity_id,
                KGEntityLink.dst_entity_id == dst_entity_id,
                KGEntityLink.link_type == link_type,
            )
            .first()
        )
        if existing:
            return _merge(existing)
        sp = session.begin_nested()
        try:
            session.add(
                KGEntityLink(
                    id=str(uuid.uuid4()),
                    src_entity_id=src_entity_id,
                    dst_entity_id=dst_entity_id,
                    link_type=link_type,
                    confidence=confidence,
                    evidence_refs=[evidence_ref],
                    metadata_json=metadata or {},
                    extraction_method=extraction_method,
                    is_valid=True,
                    created_at=_utcnow(),
                    updated_at=_utcnow(),
                )
            )
            session.flush()
            sp.commit()
        except IntegrityError:
            sp.rollback()
            existing = (
                session.query(KGEntityLink)
                .filter(
                    KGEntityLink.src_entity_id == src_entity_id,
                    KGEntityLink.dst_entity_id == dst_entity_id,
                    KGEntityLink.link_type == link_type,
                )
                .first()
            )
            if existing:
                return _merge(existing)
        except OperationalError:
            raise
        return True

    def merge_structural_link(
        self,
        session: Session,
        *,
        src_entity_id: str,
        dst_entity_id: str,
        link_type: str,
        confidence: float,
        evidence_ref: dict,
        metadata: dict | None = None,
        extraction_method: str = "vision_table",
    ) -> bool:
        if src_entity_id == dst_entity_id or link_type not in STRUCTURAL_LINK_TYPES:
            return False

        def _merge(existing: KGEntityLink) -> bool:
            refs = list(existing.evidence_refs or [])
            key = (
                evidence_ref.get("source_id"),
                evidence_ref.get("chunk_id"),
                evidence_ref.get("sentence"),
            )
            if not any(
                (r.get("source_id"), r.get("chunk_id"), r.get("sentence")) == key for r in refs
            ):
                refs.append(evidence_ref)
            existing.evidence_refs = refs[:20]
            existing.confidence = max(float(existing.confidence or 0), confidence)
            existing.extraction_method = extraction_method
            existing.is_valid = True
            existing.updated_at = _utcnow()
            if metadata:
                merged = dict(existing.metadata_json or {})
                merged.update(metadata)
                existing.metadata_json = merged
            return True

        existing = (
            session.query(KGEntityLink)
            .filter(
                KGEntityLink.src_entity_id == src_entity_id,
                KGEntityLink.dst_entity_id == dst_entity_id,
                KGEntityLink.link_type == link_type,
            )
            .first()
        )
        if existing:
            return _merge(existing)
        sp = session.begin_nested()
        try:
            session.add(
                KGEntityLink(
                    id=str(uuid.uuid4()),
                    src_entity_id=src_entity_id,
                    dst_entity_id=dst_entity_id,
                    link_type=link_type,
                    confidence=confidence,
                    evidence_refs=[evidence_ref],
                    metadata_json=metadata or {},
                    extraction_method=extraction_method,
                    is_valid=True,
                    created_at=_utcnow(),
                    updated_at=_utcnow(),
                )
            )
            session.flush()
            sp.commit()
        except IntegrityError:
            sp.rollback()
            existing = (
                session.query(KGEntityLink)
                .filter(
                    KGEntityLink.src_entity_id == src_entity_id,
                    KGEntityLink.dst_entity_id == dst_entity_id,
                    KGEntityLink.link_type == link_type,
                )
                .first()
            )
            if existing:
                return _merge(existing)
        except OperationalError:
            raise
        return True

    def get_links_for_entity(
        self,
        entity_id: str,
        *,
        direction: str = "both",
        link_types: list[str] | None = None,
        limit: int = 50,
    ) -> list[KGEntityLink]:
        with self._session_factory() as session:
            q = session.query(KGEntityLink).filter(KGEntityLink.is_valid.is_(True))
            if direction == "outgoing":
                q = q.filter(KGEntityLink.src_entity_id == entity_id)
            elif direction == "incoming":
                q = q.filter(KGEntityLink.dst_entity_id == entity_id)
            else:
                from sqlalchemy import or_

                q = q.filter(
                    or_(
                        KGEntityLink.src_entity_id == entity_id,
                        KGEntityLink.dst_entity_id == entity_id,
                    )
                )
            if link_types:
                q = q.filter(KGEntityLink.link_type.in_(link_types))
            return q.order_by(KGEntityLink.confidence.desc()).limit(limit).all()

    def refresh_counts(self, entity_ids: list[str] | None = None) -> None:
        with commit_session(self._session_factory) as session:
            q = session.query(KGEntity)
            if entity_ids:
                q = q.filter(KGEntity.id.in_(entity_ids))
            entities = q.all()
            if not entities:
                return
            ids = [e.id for e in entities]
            rows = session.execute(
                select(
                    KGMention.entity_id,
                    func.count(),
                    func.count(func.distinct(KGMention.source_id)),
                )
                .where(KGMention.entity_id.in_(ids))
                .group_by(KGMention.entity_id)
            ).all()
            counts = {r[0]: (int(r[1]), int(r[2])) for r in rows}
            for ent in entities:
                mc, sc = counts.get(ent.id, (0, 0))
                ent.mention_count = mc
                ent.source_count = sc
                ent.updated_at = _utcnow()

    def get_entity(self, entity_id: str) -> KGEntity | None:
        with self._session_factory() as session:
            return session.get(KGEntity, entity_id)

    def search_entities(
        self,
        q: str,
        *,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[KGEntity]:
        from sqlalchemy import or_

        term = (q or "").strip()
        if not term:
            return []
        like = f"%{_escape_like(term.lower())}%"
        with self._session_factory() as session:
            query = session.query(KGEntity).filter(
                or_(
                    func.lower(KGEntity.canonical_name).like(like, escape="\\"),
                    KGEntity.cas_no == term,
                    func.lower(KGEntity.linked_product_key).like(like, escape="\\"),
                    KGEntity.formula == term,
                )
            )
            if kind:
                query = query.filter(KGEntity.kind == kind)
            return query.order_by(KGEntity.mention_count.desc()).limit(limit).all()

    def fetch_mentions_for_entities(
        self,
        entity_ids: list[str],
        *,
        scan_limit: int = 500,
        project_id: str | None = None,
    ) -> tuple[list[KGMention], int, bool]:
        if not entity_ids:
            return [], 0, False
        from .models import DocumentChunk, SourceDocument

        with self._session_factory() as session:
            q = (
                session.query(KGMention)
                .filter(KGMention.entity_id.in_(entity_ids))
                .order_by(KGMention.created_at.desc())
            )
            if project_id:
                q = (
                    q.join(DocumentChunk, KGMention.chunk_id == DocumentChunk.id)
                    .join(SourceDocument, DocumentChunk.source_id == SourceDocument.id)
                    .filter(
                        (SourceDocument.project_id == project_id)
                        | (SourceDocument.project_id.is_(None))
                    )
                )
            total = q.count()
            truncated = total > scan_limit
            rows = q.limit(scan_limit).all()
            return rows, total, truncated

    def stats(self) -> dict:
        with self._session_factory() as session:
            entities = session.query(func.count(KGEntity.id)).scalar() or 0
            mentions = session.query(func.count(KGMention.id)).scalar() or 0
            links = session.query(func.count(KGEntityLink.id)).scalar() or 0
            by_kind = dict(
                session.query(KGEntity.kind, func.count(KGEntity.id))
                .group_by(KGEntity.kind)
                .all()
            )
            by_link_type = dict(
                session.query(KGEntityLink.link_type, func.count(KGEntityLink.id))
                .group_by(KGEntityLink.link_type)
                .all()
            )
            return {
                "entities": int(entities),
                "mentions": int(mentions),
                "links": int(links),
                "entities_by_kind": {k: int(v) for k, v in by_kind.items()},
                "links_by_type": {k: int(v) for k, v in by_link_type.items()},
            }

    def linked_dst_ids(self, src_entity_id: str) -> list[str]:
        with self._session_factory() as session:
            rows = (
                session.query(KGEntityLink.dst_entity_id)
                .filter(KGEntityLink.src_entity_id == src_entity_id)
                .all()
            )
            return [r[0] for r in rows]


_store: EntityStore | None = None


def get_entity_store() -> EntityStore:
    global _store
    if _store is None:
        from .database import default_session_factory

        _store = EntityStore(default_session_factory())
    return _store

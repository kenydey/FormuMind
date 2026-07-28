"""Referential integrity checks for the soft references.

Most cross-table references in this schema are plain string columns with no
foreign key, and several of them cannot become one: when
``FORMUMIND_CAMPAIGN_BACKEND=datalab`` the row a reference points at lives in
an external ELN, so a database-level constraint would reject perfectly valid
data. The honest alternative to a constraint you cannot enforce is a check you
run — otherwise orphans accumulate silently and the first symptom is a
retrieval that quietly returns less than it should.

``doe_plans.experiment_id`` is called out separately: it is declared
``String(36)`` while ``experiments.id`` is an autoincrement integer, so the
two can never join. That is a schema bug, not drift, and no amount of
reconciliation fixes it — it is reported so it stops being invisible.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker


@dataclass
class OrphanReport:
    reference: str  # "child_table.column -> parent_table.column"
    orphans: int = 0
    checked: int = 0
    note: str = ""
    # True when the columns cannot join at all, so `orphans` is not meaningful.
    unjoinable: bool = False

    @property
    def healthy(self) -> bool:
        return not self.unjoinable and self.orphans == 0


@dataclass
class IntegrityReport:
    references: list[OrphanReport] = field(default_factory=list)
    external_backend: bool = False

    @property
    def total_orphans(self) -> int:
        return sum(r.orphans for r in self.references if not r.unjoinable)

    @property
    def healthy(self) -> bool:
        return all(r.healthy for r in self.references)

    def problems(self) -> list[OrphanReport]:
        return [r for r in self.references if not r.healthy]


def _count_orphans(
    session: Session,
    child_model,
    child_col,
    parent_model,
    parent_col,
    *,
    skip_null: bool = True,
) -> tuple[int, int]:
    """(orphans, checked) — child rows whose reference has no parent."""
    query = select(func.count()).select_from(child_model)
    if skip_null:
        query = query.where(child_col.is_not(None), child_col != "")
    checked = int(session.execute(query).scalar() or 0)

    orphan_query = (
        select(func.count())
        .select_from(child_model)
        .where(~select(parent_model).where(parent_col == child_col).exists())
    )
    if skip_null:
        orphan_query = orphan_query.where(child_col.is_not(None), child_col != "")
    orphans = int(session.execute(orphan_query).scalar() or 0)
    return orphans, checked


def check_integrity(session_factory: sessionmaker[Session] | None = None) -> IntegrityReport:
    """Scan every soft reference and report orphans."""
    from ..config import get_settings
    from .database import default_session_factory
    from .models import (
        Campaign,
        DocumentChunk,
        DOEPlanRow,
        ExperimentRow,
        KGEntity,
        KGEntityLink,
        KGMention,
        SourceDocument,
    )

    factory = session_factory or default_session_factory()
    settings = get_settings()
    external = settings.campaign_backend.lower() == "datalab" or (
        settings.experiment_backend.lower() == "datalab"
    )
    report = IntegrityReport(external_backend=external)

    checks = [
        ("document_chunks.source_id -> source_documents.id",
         DocumentChunk, DocumentChunk.source_id, SourceDocument, SourceDocument.id, ""),
        ("kb_mentions.entity_id -> kb_entities.id",
         KGMention, KGMention.entity_id, KGEntity, KGEntity.id, ""),
        ("kb_mentions.source_id -> source_documents.id",
         KGMention, KGMention.source_id, SourceDocument, SourceDocument.id, ""),
        ("kb_mentions.chunk_id -> document_chunks.id",
         KGMention, KGMention.chunk_id, DocumentChunk, DocumentChunk.id, ""),
        ("kb_entity_links.src_entity_id -> kb_entities.id",
         KGEntityLink, KGEntityLink.src_entity_id, KGEntity, KGEntity.id, ""),
        ("kb_entity_links.dst_entity_id -> kb_entities.id",
         KGEntityLink, KGEntityLink.dst_entity_id, KGEntity, KGEntity.id, ""),
        ("doe_plans.campaign_id -> campaigns.id",
         DOEPlanRow, DOEPlanRow.campaign_id, Campaign, Campaign.id,
         "campaign rows are local even under the Datalab backend"),
    ]

    with factory() as session:
        for reference, child, child_col, parent, parent_col, note in checks:
            try:
                orphans, checked = _count_orphans(
                    session, child, child_col, parent, parent_col
                )
                report.references.append(
                    OrphanReport(reference=reference, orphans=orphans, checked=checked, note=note)
                )
            except Exception as exc:
                logger.debug("integrity: {} failed ({})", reference, exc)
                report.references.append(
                    OrphanReport(reference=reference, note=f"check failed: {exc}", unjoinable=True)
                )

        # Declared String(36) against an Integer primary key — these columns
        # cannot be compared, so counting orphans would be meaningless.
        try:
            with_ref = int(
                session.execute(
                    select(func.count())
                    .select_from(DOEPlanRow)
                    .where(DOEPlanRow.experiment_id.is_not(None), DOEPlanRow.experiment_id != "")
                ).scalar()
                or 0
            )
        except Exception:
            with_ref = 0
        report.references.append(
            OrphanReport(
                reference="doe_plans.experiment_id -> experiments.id",
                checked=with_ref,
                unjoinable=True,
                note=(
                    "类型不匹配：doe_plans.experiment_id 声明为 String(36)，"
                    "而 experiments.id 是自增整数，两者无法关联"
                ),
            )
        )

        # Datalab-authoritative: sample_refs point at rows in an external ELN,
        # so a missing local row is expected rather than an orphan.
        try:
            campaigns_with_refs = int(
                session.execute(
                    select(func.count()).select_from(Campaign).where(Campaign.sample_refs != [])
                ).scalar()
                or 0
            )
        except Exception:
            campaigns_with_refs = 0
        report.references.append(
            OrphanReport(
                reference="campaigns.sample_refs -> Datalab item_id",
                checked=campaigns_with_refs,
                unjoinable=True,
                note="Datalab 为该数据的真相源，本地无对应行属正常",
            )
        )

    return report


def purge_orphans(session_factory: sessionmaker[Session] | None = None) -> dict[str, int]:
    """Delete orphaned child rows for the references that are safe to clean.

    Restricted to the knowledge-base tables, which are derived data that can be
    rebuilt by re-indexing. Nothing experiment- or campaign-shaped is touched:
    those may legitimately reference an external system, and deleting a row
    because a lookup failed would destroy the very data this is meant to guard.
    """
    from .database import default_session_factory
    from .models import DocumentChunk, KGEntity, KGMention, SourceDocument
    from .session_utils import commit_session

    factory = session_factory or default_session_factory()
    deleted: dict[str, int] = {}
    with commit_session(factory) as session:
        chunk_orphans = (
            session.query(DocumentChunk)
            .filter(~select(SourceDocument).where(SourceDocument.id == DocumentChunk.source_id).exists())
            .delete(synchronize_session=False)
        )
        deleted["document_chunks"] = int(chunk_orphans or 0)

        mention_orphans = (
            session.query(KGMention)
            .filter(~select(KGEntity).where(KGEntity.id == KGMention.entity_id).exists())
            .delete(synchronize_session=False)
        )
        deleted["kb_mentions"] = int(mention_orphans or 0)
    return deleted

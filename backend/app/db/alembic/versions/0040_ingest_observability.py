"""Add ingest observability columns to ``source_documents`` (P1-1).

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-01

Third-party log review follow-up (P1-1): ingest failures were only visible in
the in-memory batch summary (``doc["error"]``) and the task result — there was
no queryable per-document failure record, and ``skipped`` had no sub-reasons.

Upgrade:
  1. Adds nullable ``ingest_status`` VARCHAR(16) — 'indexed' | 'failed'
     (NULL = legacy / unknown / non-ingest path).
  2. Adds nullable ``ingest_error`` TEXT — last ingest failure reason
     (truncated at write time).

Both columns are nullable and idempotent (guard on existing columns), so a
re-run and ``create_all``-created databases are safe. Downgrade drops them.

Semantics (see ``SourceStore.record_ingest_failure``):
  - A document that fails fetch/index gets a row (upserted by origin_url)
    with ingest_status='failed' + ingest_error.
  - Tier-1 origin dedup ignores failed rows, so a failed document is still
    retried on the next run; on success the failed row is revived in place
    (ingest_status='indexed', ingest_error=NULL) instead of duplicating.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    if not _has_column("source_documents", "ingest_status"):
        op.add_column(
            "source_documents",
            sa.Column("ingest_status", sa.String(16), nullable=True),
        )
    if not _has_column("source_documents", "ingest_error"):
        op.add_column(
            "source_documents",
            sa.Column("ingest_error", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    if _has_column("source_documents", "ingest_error"):
        op.drop_column("source_documents", "ingest_error")
    if _has_column("source_documents", "ingest_status"):
        op.drop_column("source_documents", "ingest_status")

"""Add ``parser`` provenance column to ``source_documents``.

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-30

Phase 3 (P3-5): record which parser tier produced each document's text
(hybrid | docling | marker | mineru | rapidocr | markitdown | pypdf |
docx | text | none) so tier output is auditable in the DB instead of
only in logs. NULL = unknown / non-parse ingest path (API raw text, QC
reports).

Nullable, non-breaking. Idempotent: guarded by sqlalchemy.inspect
existence check (mirrors the 0032/0033/0034 pattern). Downgrade drops
the column.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _existing_columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    existing = _existing_columns("source_documents")
    if not existing:
        return
    if "parser" not in existing:
        op.add_column(
            "source_documents",
            sa.Column("parser", sa.String(32), nullable=True),
        )


def downgrade() -> None:
    existing = _existing_columns("source_documents")
    if not existing or "parser" not in existing:
        return
    op.drop_column("source_documents", "parser")

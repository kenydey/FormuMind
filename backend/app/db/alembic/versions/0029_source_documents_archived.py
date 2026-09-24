"""source_documents.archived — KB soft-archive (W3).

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-24

Soft-archive hides a source from default Hub lists and hybrid/keyword
retrieval while retaining SourceDocument + DocumentChunk rows. Hard delete
(``DELETE /api/kb/sources/{id}``) remains the permanent path.
Idempotent: column/index created only when absent.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def _has_column(bind, table: str, column: str) -> bool:
    cols = {c["name"] for c in sa.inspect(bind).get_columns(table)}
    return column in cols


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "source_documents") and not _has_column(
        bind, "source_documents", "archived"
    ):
        op.add_column(
            "source_documents",
            sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        op.create_index("ix_source_documents_archived", "source_documents", ["archived"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "source_documents") and _has_column(
        bind, "source_documents", "archived"
    ):
        op.drop_index("ix_source_documents_archived", table_name="source_documents")
        op.drop_column("source_documents", "archived")

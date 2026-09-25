"""source_documents.archived_at — KB retention clock (W4).

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-25

Nullable timestamp set when soft-archive flips on; cleared on restore.
Idempotent: column/index created only when absent.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
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
        bind, "source_documents", "archived_at"
    ):
        op.add_column(
            "source_documents",
            sa.Column("archived_at", sa.DateTime(), nullable=True),
        )
        op.create_index(
            "ix_source_documents_archived_at", "source_documents", ["archived_at"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "source_documents") and _has_column(
        bind, "source_documents", "archived_at"
    ):
        op.drop_index("ix_source_documents_archived_at", table_name="source_documents")
        op.drop_column("source_documents", "archived_at")

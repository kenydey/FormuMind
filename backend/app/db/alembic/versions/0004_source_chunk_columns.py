"""Add origin/page columns to ``source_documents`` and ``document_chunks``.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-26

Idempotent: fresh databases already get these columns from the 0001 baseline
(which reflects the current ORM models), so each ``op.add_column`` is guarded
by an ``sqlalchemy.inspect`` column-existence check. Legacy databases stamped
at 0001 receive the missing columns here.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_NEW_COLUMNS: dict[str, tuple[sa.Column, ...]] = {
    "source_documents": (
        sa.Column("origin_url", sa.String(1024), nullable=True),
        sa.Column("project_id", sa.String(64), nullable=True),
    ),
    "document_chunks": (
        sa.Column("page_no", sa.Integer, nullable=True),
        sa.Column("meta", sa.JSON, nullable=True),
    ),
}


def _existing_columns(table: str) -> set[str]:
    """Return the column names currently present on ``table`` (empty if absent)."""
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    """Add missing columns to both tables, skipping any that already exist."""
    for table, columns in _NEW_COLUMNS.items():
        existing = _existing_columns(table)
        for column in columns:
            if column.name not in existing:
                op.add_column(table, column)


def downgrade() -> None:
    """No-op: adding nullable columns is irreversible but harmless.

    Dropping columns would break legacy databases that legitimately contain
    data in them, so the downgrade deliberately leaves the schema untouched.
    """

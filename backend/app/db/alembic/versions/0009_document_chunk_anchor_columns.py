"""Add anchor columns (offset_start/end, paragraph_idx) to ``document_chunks``.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-27

Idempotent: fresh databases already get these columns from the 0001 baseline
(which reflects the current ORM models), so each ``op.add_column`` is guarded
by an ``sqlalchemy.inspect`` column-existence check. Legacy databases stamped
at 0001 receive the missing columns here.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_NEW_COLUMNS: tuple[sa.Column, ...] = (
    sa.Column("offset_start", sa.Integer, nullable=True),
    sa.Column("offset_end", sa.Integer, nullable=True),
    sa.Column("paragraph_idx", sa.Integer, nullable=True),
)


def _existing_columns(table: str) -> set[str]:
    """Return the column names currently present on ``table`` (empty if absent)."""
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def _existing_indexes(table: str) -> set[str]:
    """Return the index names currently present on ``table`` (empty if absent)."""
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {ix["name"] for ix in inspector.get_indexes(table)}


def upgrade() -> None:
    """Add missing anchor columns and the source-offset index, skipping existing ones."""
    existing = _existing_columns("document_chunks")
    for column in _NEW_COLUMNS:
        if column.name not in existing:
            op.add_column("document_chunks", column)

    # Composite index for efficient range queries (source_id + offset_start).
    existing_indexes = _existing_indexes("document_chunks")
    if "ix_document_chunks_source_offset" not in existing_indexes:
        op.create_index(
            "ix_document_chunks_source_offset",
            "document_chunks",
            ["source_id", "offset_start"],
        )


def downgrade() -> None:
    """No-op: adding nullable columns is irreversible but harmless.

    Dropping columns would break legacy databases that legitimately contain
    data in them, so the downgrade deliberately leaves the schema untouched.
    """

"""Add ``item_id`` and ``project_id`` columns to ``experiments``.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-26

Idempotent: fresh databases already get these columns from the 0001 baseline
(which reflects the current ORM models), so each ``op.add_column`` is guarded
by an ``sqlalchemy.inspect`` column-existence check. Legacy databases stamped
at 0001 receive the missing columns here.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


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
    """Add the missing ``experiments`` columns and indexes, skipping existing ones."""
    existing = _existing_columns("experiments")
    if "item_id" not in existing:
        op.add_column("experiments", sa.Column("item_id", sa.String(128), nullable=True))
    if "project_id" not in existing:
        op.add_column(
            "experiments",
            sa.Column("project_id", sa.String(36), nullable=False, server_default=""),
        )

    # The 0001 baseline only creates the ORM ``index=True`` indexes on fresh
    # databases; backfill them here for legacy databases to eliminate drift.
    existing_indexes = _existing_indexes("experiments")
    if "ix_experiments_item_id" not in existing_indexes:
        op.create_index("ix_experiments_item_id", "experiments", ["item_id"])
    if "ix_experiments_project_id" not in existing_indexes:
        op.create_index("ix_experiments_project_id", "experiments", ["project_id"])


def downgrade() -> None:
    """No-op: adding nullable/defaulted columns is irreversible but harmless.

    Dropping columns would break legacy databases that legitimately contain
    data in them, so the downgrade deliberately leaves the schema untouched.
    """

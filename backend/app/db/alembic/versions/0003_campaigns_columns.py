"""Add project/metric/snapshot columns to ``campaigns``.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-26

Idempotent: fresh databases already get these columns from the 0001 baseline
(which reflects the current ORM models), so each ``op.add_column`` is guarded
by an ``sqlalchemy.inspect`` column-existence check. Legacy databases stamped
at 0001 receive the missing columns here.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_NEW_COLUMNS: tuple[sa.Column, ...] = (
    sa.Column("project_id", sa.String(36), nullable=True),
    sa.Column("primary_metric", sa.String(64), nullable=True),
    sa.Column("objectives_snapshot", sa.JSON, nullable=True),
    sa.Column("lever_snapshot", sa.JSON, nullable=True),
    sa.Column("sample_refs", sa.JSON, nullable=False, server_default="[]"),
    sa.Column("loop_history", sa.JSON, nullable=False, server_default="[]"),
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
    """Add the missing ``campaigns`` columns and indexes, skipping existing ones."""
    existing = _existing_columns("campaigns")
    for column in _NEW_COLUMNS:
        if column.name not in existing:
            op.add_column("campaigns", column)

    # The 0001 baseline only creates the ORM ``index=True`` index on fresh
    # databases; backfill it here for legacy databases to eliminate drift.
    if "ix_campaigns_project_id" not in _existing_indexes("campaigns"):
        op.create_index("ix_campaigns_project_id", "campaigns", ["project_id"])


def downgrade() -> None:
    """No-op: adding nullable/defaulted columns is irreversible but harmless.

    Dropping columns would break legacy databases that legitimately contain
    data in them, so the downgrade deliberately leaves the schema untouched.
    """

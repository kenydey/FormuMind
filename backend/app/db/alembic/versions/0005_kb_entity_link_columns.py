"""Add curation/provenance columns to ``kb_entity_links``.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-26

Idempotent: fresh databases already get these columns from the 0001 baseline
(which reflects the current ORM models), so each ``op.add_column`` is guarded
by an ``sqlalchemy.inspect`` column-existence check. Legacy databases stamped
at 0001 receive the missing columns here.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_NEW_COLUMNS: tuple[sa.Column, ...] = (
    sa.Column("metadata_json", sa.JSON, nullable=False, server_default="{}"),
    sa.Column("is_valid", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("extraction_method", sa.String(16), nullable=False, server_default="rule"),
    sa.Column("updated_at", sa.DateTime, nullable=True),
)


def _existing_columns(table: str) -> set[str]:
    """Return the column names currently present on ``table`` (empty if absent)."""
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    """Add the missing ``kb_entity_links`` columns, skipping any that exist."""
    existing = _existing_columns("kb_entity_links")
    for column in _NEW_COLUMNS:
        if column.name not in existing:
            op.add_column("kb_entity_links", column)


def downgrade() -> None:
    """No-op: adding defaulted columns is irreversible but harmless.

    Dropping columns would break legacy databases that legitimately contain
    data in them, so the downgrade deliberately leaves the schema untouched.
    """

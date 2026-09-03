"""Add ``round`` column to ``doe_plans`` (closed-loop round provenance).

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-25

Tracks which closed-loop round each DOE plan was generated for, so the
workbench can group DOE plans + ledger rows by round. NULL = manual DOE or
legacy orphan rows (never back-inferred).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _existing_columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    existing = _existing_columns("doe_plans")
    if "round" not in existing:
        op.add_column("doe_plans", sa.Column("round", sa.Integer, nullable=True))


def downgrade() -> None:
    """No-op: adding a nullable column is harmless and irreversible."""

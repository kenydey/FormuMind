"""Add ``materials.suppliers_json`` — per-supplier sourcing detail.

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-12

Holds the structured supplier list harvested for a material (name, URL,
product page, price, stock, delivery days, country …) as a JSON array so the
material catalog can carry sourcing intelligence without a second table.
Mirrors the existing ``regulatory`` JSON column.

Idempotent: the column is added only when absent.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

TABLE = "materials"
COLUMN = "suppliers_json"


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns(TABLE)}
    if COLUMN not in cols:
        op.add_column(TABLE, sa.Column(COLUMN, sa.JSON, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns(TABLE)}
    if COLUMN in cols:
        op.drop_column(TABLE, COLUMN)
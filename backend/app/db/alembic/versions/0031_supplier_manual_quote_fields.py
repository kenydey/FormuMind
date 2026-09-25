"""A′ manual supplier commercial fields on suppliers / material_suppliers.

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-25

Adds:
  - suppliers.country
  - material_suppliers: currency, price_cny_per_kg, price_source,
    price_observed_at, moq, pack_size, lead_time_days

All nullable (or default manual). No scrape. Idempotent ALTER.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def _has_column(bind, table: str, column: str) -> bool:
    cols = {c["name"] for c in sa.inspect(bind).get_columns(table)}
    return column in cols


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "suppliers") and not _has_column(bind, "suppliers", "country"):
        op.add_column("suppliers", sa.Column("country", sa.String(64), nullable=True))

    if _has_table(bind, "material_suppliers"):
        cols = [
            ("currency", sa.Column("currency", sa.String(8), nullable=True)),
            ("price_cny_per_kg", sa.Column("price_cny_per_kg", sa.Float(), nullable=True)),
            (
                "price_source",
                sa.Column(
                    "price_source",
                    sa.String(32),
                    nullable=False,
                    server_default="manual",
                ),
            ),
            (
                "price_observed_at",
                sa.Column("price_observed_at", sa.DateTime(), nullable=True),
            ),
            ("moq", sa.Column("moq", sa.String(64), nullable=True)),
            ("pack_size", sa.Column("pack_size", sa.String(64), nullable=True)),
            ("lead_time_days", sa.Column("lead_time_days", sa.Integer(), nullable=True)),
        ]
        for name, col in cols:
            if not _has_column(bind, "material_suppliers", name):
                op.add_column("material_suppliers", col)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "material_suppliers"):
        for name in (
            "lead_time_days",
            "pack_size",
            "moq",
            "price_observed_at",
            "price_source",
            "price_cny_per_kg",
            "currency",
        ):
            if _has_column(bind, "material_suppliers", name):
                op.drop_column("material_suppliers", name)
    if _has_table(bind, "suppliers") and _has_column(bind, "suppliers", "country"):
        op.drop_column("suppliers", "country")

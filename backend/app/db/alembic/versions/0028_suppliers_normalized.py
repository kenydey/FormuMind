"""Normalize material suppliers out of ``materials.suppliers_json``.

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-14

Adds:
  - ``suppliers`` — deduplicated vendor master (norm_name unique)
  - ``material_suppliers`` — material↔supplier link (product_url per link)

``materials.suppliers_json`` is retained as a transitional projection / fallback
for older readers; application code dual-writes and can clear the JSON after
backfill. Idempotent create (skip if tables already exist).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "suppliers" not in tables:
        op.create_table(
            "suppliers",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("norm_name", sa.String(200), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("url", sa.String(1024), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("norm_name", name="uq_suppliers_norm_name"),
        )
        op.create_index("ix_suppliers_norm_name", "suppliers", ["norm_name"])
        op.create_index("ix_suppliers_name", "suppliers", ["name"])

    if "material_suppliers" not in tables:
        op.create_table(
            "material_suppliers",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "material_id",
                sa.String(36),
                sa.ForeignKey("materials.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "supplier_id",
                sa.String(36),
                sa.ForeignKey("suppliers.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("product_url", sa.String(1024), nullable=False, server_default=""),
            sa.Column("source", sa.String(32), nullable=False, server_default="app"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "material_id",
                "supplier_id",
                "product_url",
                name="uq_material_supplier_product",
            ),
        )
        op.create_index(
            "ix_material_suppliers_material_id", "material_suppliers", ["material_id"]
        )
        op.create_index(
            "ix_material_suppliers_supplier_id", "material_suppliers", ["supplier_id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "material_suppliers" in tables:
        op.drop_table("material_suppliers")
    if "suppliers" in tables:
        op.drop_table("suppliers")

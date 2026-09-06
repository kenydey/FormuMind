"""materials.archived + material_candidates (catalog expansion queue).

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-06

Idempotent: column/table created only when absent.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def _has_column(bind, table: str, column: str) -> bool:
    cols = {c["name"] for c in sa.inspect(bind).get_columns(table)}
    return column in cols


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "materials") and not _has_column(bind, "materials", "archived"):
        op.add_column(
            "materials",
            sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        op.create_index("ix_materials_archived", "materials", ["archived"])

    if not _has_table(bind, "material_candidates"):
        op.create_table(
            "material_candidates",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("norm_key", sa.String(200), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("role", sa.String(60), nullable=False, server_default=""),
            sa.Column("cas_no", sa.String(32), nullable=True),
            sa.Column("smiles", sa.Text(), nullable=True),
            sa.Column("formula", sa.String(120), nullable=True),
            sa.Column("zh_name", sa.String(200), nullable=True),
            sa.Column("supplier", sa.String(120), nullable=True),
            sa.Column("source", sa.String(64), nullable=False, server_default="kb_promoted"),
            sa.Column("source_ref", sa.String(512), nullable=True),
            sa.Column("confidence", sa.String(16), nullable=False, server_default="low"),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_material_candidates_norm_key", "material_candidates", ["norm_key"], unique=True)
        op.create_index("ix_material_candidates_name", "material_candidates", ["name"])
        op.create_index("ix_material_candidates_source", "material_candidates", ["source"])
        op.create_index("ix_material_candidates_status", "material_candidates", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "material_candidates"):
        op.drop_table("material_candidates")
    if _has_table(bind, "materials") and _has_column(bind, "materials", "archived"):
        op.drop_index("ix_materials_archived", table_name="materials")
        op.drop_column("materials", "archived")

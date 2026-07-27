"""Add the ``materials`` table (raw-material registry).

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-27

Backs ``knowledge.RAW_MATERIALS``, which used to be a module-level dict
literal. Making the catalog data lets ingredient *choice* become a search
variable (inverse design, substitution) and lets the pool grow at runtime.

Idempotent: the table and each index are created only when absent, so the
script is a no-op on a database that already has them.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

TABLE = "materials"

_INDEXES = (
    ("ix_materials_norm_key", ["norm_key"], True),
    ("ix_materials_name", ["name"], False),
    ("ix_materials_cas_no", ["cas_no"], False),
    ("ix_materials_origin", ["origin"], False),
    ("ix_materials_availability", ["availability"], False),
    ("ix_materials_functional_class", ["functional_class"], False),
    ("ix_materials_substitute_group", ["substitute_group"], False),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE not in inspector.get_table_names():
        op.create_table(
            TABLE,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("norm_key", sa.String(200), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("role", sa.String(60), nullable=False, server_default=""),
            sa.Column("origin", sa.String(16), nullable=False, server_default="seed"),
            # curated chemistry
            sa.Column("formula", sa.String(120), nullable=True),
            sa.Column("smiles", sa.Text, nullable=True),
            sa.Column("cas_no", sa.String(32), nullable=True),
            sa.Column("zh_name", sa.String(200), nullable=True),
            sa.Column("molar_mass", sa.Float, nullable=True),
            sa.Column("price_cny_per_kg", sa.Float, nullable=True),
            sa.Column("voc_contrib", sa.Float, nullable=True),
            sa.Column("density_gcm3", sa.Float, nullable=True),
            sa.Column("oil_absorption", sa.Float, nullable=True),
            sa.Column("tg_k", sa.Float, nullable=True),
            sa.Column("lab", sa.JSON, nullable=True),
            sa.Column("svhc", sa.Boolean, nullable=True),
            sa.Column("carrier", sa.String(16), nullable=True),
            sa.Column("water_compatible", sa.Boolean, nullable=True),
            # substitution / sourcing metadata
            sa.Column("functional_class", sa.String(60), nullable=True),
            sa.Column("equivalent_weight", sa.Float, nullable=True),
            sa.Column("hansen_d", sa.Float, nullable=True),
            sa.Column("hansen_p", sa.Float, nullable=True),
            sa.Column("hansen_h", sa.Float, nullable=True),
            sa.Column("hlb", sa.Float, nullable=True),
            sa.Column("supplier", sa.String(120), nullable=True),
            sa.Column("lead_time_days", sa.Integer, nullable=True),
            sa.Column(
                "availability", sa.String(16), nullable=False, server_default="in_stock"
            ),
            sa.Column("regulatory", sa.JSON, nullable=True),
            sa.Column("substitute_group", sa.String(60), nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("updated_at", sa.DateTime, nullable=False),
        )

    existing = {ix["name"] for ix in sa.inspect(bind).get_indexes(TABLE)}
    for name, columns, unique in _INDEXES:
        if name not in existing:
            op.create_index(name, TABLE, columns, unique=unique)


def downgrade() -> None:
    """Drop the ``materials`` table and its indexes."""
    bind = op.get_bind()
    if TABLE in sa.inspect(bind).get_table_names():
        op.drop_table(TABLE)

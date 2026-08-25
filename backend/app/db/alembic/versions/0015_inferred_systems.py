"""Add the ``inferred_systems`` table (LLM self-learning knowledge base, P2).

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-25

Persists LLM-inferred formulation-system constraints for unknown product_types
so they are reused on later hits instead of re-inferring. Idempotent: table and
indexes are created only when absent.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

TABLE = "inferred_systems"

_INDEXES = (
    ("ix_inferred_systems_normalized_key", ["normalized_key"], True),
    ("ix_inferred_systems_status", ["status"], False),
    ("ix_inferred_systems_source_requirement_id", ["source_requirement_id"], False),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE not in inspector.get_table_names():
        op.create_table(
            TABLE,
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("normalized_key", sa.String(255), nullable=False),
            sa.Column("product_type", sa.Text, nullable=False, server_default=""),
            sa.Column("system_name", sa.String(200), nullable=False, server_default=""),
            sa.Column("must_include_roles", sa.JSON, nullable=False, server_default=sa.text("'[]'")),
            sa.Column("must_exclude", sa.Text, nullable=False, server_default=""),
            sa.Column("constraints", sa.JSON, nullable=False, server_default=sa.text("'[]'")),
            sa.Column("metric_ranges", sa.JSON, nullable=False, server_default=sa.text("'{}'")),
            sa.Column("confidence", sa.String(10), nullable=False, server_default="medium"),
            sa.Column("hit_count", sa.Integer, nullable=False, server_default="1"),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            sa.Column("source_requirement_id", sa.String(64), nullable=True),
            sa.Column("source_requirement_text", sa.Text, nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("updated_at", sa.DateTime, nullable=False),
        )

    existing = {ix["name"] for ix in sa.inspect(bind).get_indexes(TABLE)}
    for name, columns, unique in _INDEXES:
        if name not in existing:
            op.create_index(name, TABLE, columns, unique=unique)


def downgrade() -> None:
    """Drop the ``inferred_systems`` table and its indexes."""
    bind = op.get_bind()
    if TABLE in sa.inspect(bind).get_table_names():
        op.drop_table(TABLE)

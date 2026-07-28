"""Add ``formulation_versions``.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-28

Immutable revision snapshots with a self-referential parent link, so a
formulation's history — including branches — is recoverable instead of being
overwritten in place.

Idempotent: the table and each index are created only when absent.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

TABLE = "formulation_versions"

_INDEXES = (
    ("ix_formulation_versions_lineage_id", ["lineage_id"], False),
    ("ix_formulation_versions_parent_version_id", ["parent_version_id"], False),
    ("ix_formulation_versions_project_id", ["project_id"], False),
    ("ix_formulation_versions_domain", ["domain"], False),
    ("ix_formulation_versions_lineage_version", ["lineage_id", "version"], False),
)


def upgrade() -> None:
    bind = op.get_bind()
    if TABLE not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            TABLE,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("lineage_id", sa.String(36), nullable=False),
            sa.Column("version", sa.Integer, nullable=False, server_default="1"),
            # SET NULL, not CASCADE: deleting one revision must not erase its
            # descendants along with it.
            sa.Column(
                "parent_version_id",
                sa.String(36),
                sa.ForeignKey("formulation_versions.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("project_id", sa.String(64), nullable=True),
            sa.Column("name", sa.String(255), nullable=False, server_default=""),
            sa.Column("domain", sa.String(64), nullable=False),
            sa.Column("snapshot", sa.JSON, nullable=False),
            sa.Column("change_summary", sa.Text, nullable=False, server_default=""),
            sa.Column("created_by", sa.String(80), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.UniqueConstraint("lineage_id", "version", name="uq_formulation_version"),
        )

    inspector = sa.inspect(bind)
    if TABLE not in set(inspector.get_table_names()):
        return
    existing = {ix["name"] for ix in inspector.get_indexes(TABLE)}
    for name, columns, unique in _INDEXES:
        if name not in existing:
            op.create_index(name, TABLE, columns, unique=unique)


def downgrade() -> None:
    """Drop the ``formulation_versions`` table."""
    bind = op.get_bind()
    if TABLE in set(sa.inspect(bind).get_table_names()):
        op.drop_table(TABLE)

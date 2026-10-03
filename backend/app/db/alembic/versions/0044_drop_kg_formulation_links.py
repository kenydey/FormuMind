"""Drop the never-used ``kg_formulation_links`` table.

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-03

The table was meant to hang measured experiments on knowledge-graph entities. The linker was
never finished: nothing in the application writes or reads it (round-3 audit), so it only cost
a model, a migration, and the impression that the feature exists.

``0001_baseline`` builds a fresh database with ``create_all`` from the *current* models, so a
fresh database no longer gets the table at 0001; revision 0020 then creates it and this one
drops it. Both directions check first and are safe to repeat.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "kg_formulation_links"


def _has_table() -> bool:
    return _TABLE in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table():
        op.drop_table(_TABLE)


def downgrade() -> None:
    # The shape revision 0020 created.
    if _has_table():
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.Integer(),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "entity_id",
            sa.String(64),
            sa.ForeignKey("kb_entities.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("role", sa.String(60), default=""),
        sa.Column("weight_pct", sa.Float(), nullable=True),
        sa.Column("link_type", sa.String(32), default="contains"),
        sa.Column("project_id", sa.String(64), default="", index=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("experiment_id", "entity_id", "role", name="uq_kg_formulation_link"),
    )

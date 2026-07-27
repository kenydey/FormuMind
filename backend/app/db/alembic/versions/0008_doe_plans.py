"""Create the ``doe_plans`` table for persistent DOE design storage.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-27

Persists experimental designs so /baybe/recommend, /doe, and /doe/active
responses are recorded idempotently for audit / reconciliation.  The
migration is inspect-first — ``create_table`` only runs when the table
does not exist, and any missing indexes are backfilled on re-run.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create ``doe_plans`` if it does not already exist.

    After the initial ``create_table`` the migration also backfills any
    missing indexes — this handles the partial state where a previous
    ``upgrade`` crashed between creating the table and creating its
    indexes.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "doe_plans" not in inspector.get_table_names():
        op.create_table(
            "doe_plans",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("experiment_id", sa.String(36), nullable=True, index=True),
            sa.Column("campaign_id", sa.String(36), nullable=True, index=True),
            sa.Column("design_type", sa.String(32), nullable=False),
            sa.Column("parameters", sa.JSON, nullable=False, default=dict),
            sa.Column("created_at", sa.DateTime, nullable=False),
        )
    # Backfill indexes on existing tables (idempotent on second run).
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("doe_plans")}
    if "ix_doe_plans_experiment" not in existing_indexes:
        op.create_index(
            "ix_doe_plans_experiment", "doe_plans", ["experiment_id"],
        )
    if "ix_doe_plans_campaign" not in existing_indexes:
        op.create_index(
            "ix_doe_plans_campaign", "doe_plans", ["campaign_id"],
        )


def downgrade() -> None:
    """Drop the ``doe_plans`` table and its indexes."""
    op.drop_table("doe_plans")

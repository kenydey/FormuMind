"""Create the ``task_outbox`` idempotent task dispatch table.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-27

The ``task_outbox`` table is the durable outbox for async task dispatch:
every task is written once per ``(operation, idempotency_key)`` pair and
selected FIFO by the dispatcher. The migration is idempotent (inspect-first)
so alembic upgrade head is safe to rerun after a fresh DB baseline.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create ``task_outbox`` if it does not already exist.

    After the initial ``create_table`` the migration also backfills any
    indexes or unique constraint that are missing—this handles the partial
    state where a previous ``upgrade`` crashed between creating the table
    and creating its indexes / constraints.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "task_outbox" not in inspector.get_table_names():
        op.create_table(
            "task_outbox",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("operation", sa.String(64), nullable=False),
            sa.Column("idempotency_key", sa.String(128), nullable=False),
            sa.Column("payload", sa.JSON, nullable=False, default=dict),
            sa.Column("status", sa.String(16), nullable=False, default="PENDING"),
            sa.Column("claimed_by", sa.String(128), nullable=True),
            sa.Column("claimed_at", sa.DateTime, nullable=True),
            sa.Column("attempt_count", sa.Integer, nullable=False, default=0),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("updated_at", sa.DateTime, nullable=False),
            sa.UniqueConstraint("operation", "idempotency_key", name="uq_task_outbox_op_key"),
        )
    # Backfill indexes / constraint on existing tables (idempotent on second run).
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("task_outbox")}
    if "ix_task_outbox_status_created" not in existing_indexes:
        op.create_index(
            "ix_task_outbox_status_created", "task_outbox",
            ["status", "created_at"],
        )
    if "ix_task_outbox_claim" not in existing_indexes:
        op.create_index(
            "ix_task_outbox_claim", "task_outbox",
            ["claimed_by", "claimed_at"],
        )
    existing_constraints = {
        c["name"] for c in inspector.get_unique_constraints("task_outbox")
    }
    if "uq_task_outbox_op_key" not in existing_constraints:
        op.create_unique_constraint(
            "uq_task_outbox_op_key", "task_outbox",
            ["operation", "idempotency_key"],
        )


def downgrade() -> None:
    """No-op. Dropping the table would lose durable task state."""

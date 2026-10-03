"""Add ``task_id`` to ``task_outbox`` (recover under the original Celery id).

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-03

Crash recovery used to re-dispatch an interrupted job with a fresh Celery id, so the
client that had been handed the original id kept polling a task that would never
finish while the replayed run completed under an id nobody knew. The outbox row now
remembers the id; recovery calls ``apply_async(task_id=...)`` with it.

Nullable and without backfill: rows written before this revision (and rows whose
id was never recorded) are re-dispatched under a new id exactly as before.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    # Partial / synthetic legacy schemas may not have the outbox yet.
    if not _has_table("task_outbox"):
        return
    if not _has_column("task_outbox", "task_id"):
        op.add_column("task_outbox", sa.Column("task_id", sa.String(64), nullable=True))


def downgrade() -> None:
    if _has_table("task_outbox") and _has_column("task_outbox", "task_id"):
        with op.batch_alter_table("task_outbox") as batch:
            batch.drop_column("task_id")

"""Add unique constraint ``uq_document_chunks_source_ord`` on (source_id, ord).

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-27

Idempotent: inspects existing unique constraints and skips when already
present.  SQLite lacks ``ALTER TABLE ADD CONSTRAINT``, so the constraint is
applied via batch_alter_table (copy-and-move strategy).  Legacy databases
stamped at 0001 that already have the constraint from the 0001 baseline
(which reflects the current ORM) are handled by the inspect guard.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

CONSTRAINT_NAME = "uq_document_chunks_source_ord"
TABLE = "document_chunks"


def _existing_constraints(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_unique_constraints(table)}


def upgrade() -> None:
    if CONSTRAINT_NAME in _existing_constraints(TABLE):
        return

    # Dedupe existing rows before the batch copy — duplicate (source_id, ord)
    # rows would violate the constraint during the INSERT-into-new-table step.
    # Keeps the row with the smallest id for each group.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table(TABLE):
        bind.execute(
            sa.text(
                f"""
                DELETE FROM {TABLE}
                WHERE id NOT IN (
                    SELECT MIN(id) FROM {TABLE} GROUP BY source_id, ord
                )
                """
            )
        )

    with op.batch_alter_table(TABLE) as batch_op:
        batch_op.create_unique_constraint(CONSTRAINT_NAME, ["source_id", "ord"])


def downgrade() -> None:
    if CONSTRAINT_NAME not in _existing_constraints(TABLE):
        return

    with op.batch_alter_table(TABLE) as batch_op:
        batch_op.drop_constraint(CONSTRAINT_NAME)

"""Add ``kb_coverage_counters`` table for persistent KB coverage counters.

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-30

Phase B (B-3): the KB embedding coverage counters
(``kb_chunks_embedded`` / ``kb_chunks_total``) move from a process-local dict
in ``app.services.kb_index`` to SQLite, so they survive restarts and stay
consistent across processes.

Idempotent: guarded by sqlalchemy.inspect table-existence check (mirrors
the 0032–0035 pattern). Downgrade drops the table.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "kb_coverage_counters" in inspector.get_table_names():
        return
    op.create_table(
        "kb_coverage_counters",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "kb_coverage_counters" not in inspector.get_table_names():
        return
    op.drop_table("kb_coverage_counters")

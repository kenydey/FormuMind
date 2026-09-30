"""Add ``status`` lifecycle column to ``doe_plans``.

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-01

Phase C (C-4b): DOE plans get a lifecycle status — draft → active →
completed, draft/active → aborted. Existing rows are all backfilled as
``draft``: historical plans were generated but never tracked through a
lifecycle, so inferring active/completed would fabricate history.

Idempotent: guarded by column-existence check (mirrors the 0032–0036
pattern). Downgrade drops the column and index.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    if not _has_column("doe_plans", "status"):
        op.add_column(
            "doe_plans",
            sa.Column(
                "status", sa.String(16), nullable=False, server_default="draft"
            ),
        )
    inspector = sa.inspect(op.get_bind())
    index_names = {ix["name"] for ix in inspector.get_indexes("doe_plans")}
    if "ix_doe_plans_status" not in index_names:
        op.create_index("ix_doe_plans_status", "doe_plans", ["status"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    index_names = {ix["name"] for ix in inspector.get_indexes("doe_plans")}
    if "ix_doe_plans_status" in index_names:
        op.drop_index("ix_doe_plans_status", table_name="doe_plans")
    if _has_column("doe_plans", "status"):
        op.drop_column("doe_plans", "status")

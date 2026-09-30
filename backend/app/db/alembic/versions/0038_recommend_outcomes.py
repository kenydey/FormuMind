"""Create ``recommend_outcomes`` table for recommendation adopt telemetry.

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-01

Phase C (C-8): weak-success layer (user adoption) of the two-tier
"recommendation success" definition. Rows are created by round registration
at POST /formulations/recommend time (adopted=False — the denominator) and
flipped to adopted by POST /formulations/recommend/{id}/adopt. Aggregated
by GET /api/ops/recommend-stats (fail-open). The strong-success layer
(experiment validation) is pending C-5 — no ``validated`` column is added
here; the ops response reserves the field as null-by-contract for now.

Idempotent: guarded by table-existence check (mirrors the 0032–0037
pattern). Downgrade drops the table.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "recommend_outcomes" in inspector.get_table_names():
        return
    op.create_table(
        "recommend_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("recommend_id", sa.String(32), nullable=False, unique=True),
        sa.Column("project_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        # C-8: rounds register as adopted=False (the denominator); adopt
        # flips to True. Default False keeps adopt_rate honest.
        sa.Column("adopted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("adopt_signal", sa.String(16), nullable=False, server_default="button"),
        sa.Column("formula_snapshot", sa.JSON(), nullable=False),
        sa.Column("formula_hash", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_recommend_outcomes_recommend_id", "recommend_outcomes", ["recommend_id"]
    )
    op.create_index(
        "ix_recommend_outcomes_project", "recommend_outcomes", ["project_id"]
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "recommend_outcomes" not in inspector.get_table_names():
        return
    op.drop_table("recommend_outcomes")

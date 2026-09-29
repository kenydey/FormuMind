"""Add objective-tracking columns to ``doe_cycle_runs``.

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-29

Phase 2 (P2-4): per-cycle best-vs-target observability for the closed-loop
DOE convergence work.
- ``best_objective_value`` — best measured value of the primary objective
  metric among prior measurements at cycle time (NULL = no measurements).
- ``target_value`` — the objective target at cycle time (NULL = no target).
- ``objective_metric`` / ``objective_direction`` — what was tracked.
- ``convergence_reason`` — "" | rmse_plateau | target_achieved |
  budget_exhausted ("" = normal generation cycle).

All columns nullable / defaulted so the upgrade is non-breaking.
Idempotent: every DDL is guarded by a sqlalchemy.inspect existence check
(mirrors the 0032/0033 pattern). Downgrade drops the added columns.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_NEW_COLUMNS: tuple[sa.Column, ...] = (
    sa.Column("best_objective_value", sa.Float(), nullable=True),
    sa.Column("target_value", sa.Float(), nullable=True),
    sa.Column("objective_metric", sa.String(64), nullable=True),
    sa.Column("objective_direction", sa.String(16), nullable=True),
    sa.Column("convergence_reason", sa.String(32), nullable=True),
)


def _existing_columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    existing = _existing_columns("doe_cycle_runs")
    if not existing:
        return
    for col in _NEW_COLUMNS:
        if col.name not in existing:
            op.add_column("doe_cycle_runs", col)


def downgrade() -> None:
    existing = _existing_columns("doe_cycle_runs")
    if not existing:
        return
    for col in _NEW_COLUMNS:
        if col.name in existing:
            op.drop_column("doe_cycle_runs", col.name)

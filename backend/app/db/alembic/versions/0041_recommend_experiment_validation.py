"""Add experiment-validation columns to ``recommend_outcomes`` (U-4).

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-02

U-4 adopt 双层信号：C-8 的弱成功层（用户采纳）已在 C-a 建表，本迁移加
强成功层（实验验证）的三列：

  1. ``experiment_validated`` BOOLEAN NOT NULL DEFAULT FALSE —— 该被采纳
     配方是否已有 lab 测量经 sync-datalab 同步入库。
  2. ``validated_at`` DATETIME NULL —— 首次命中验证的时间。
  3. ``validation_summary`` JSON NULL —— 命中实验的摘要
     {experiment_id, measured_keys, matched_ingredients}。

匹配是启发式的（实验行 factors 的成分名集合 vs 采纳快照的成分名集合，
Jaccard ≥ 0.8），见 ``recommend_outcome_store.try_mark_experiment_validated``。
匹配不上是正常情况，不报错。

All columns are nullable-or-defaulted and idempotent (guard on existing
columns), so a re-run and ``create_all``-created databases are safe.
Downgrade drops them.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    if not _has_column("recommend_outcomes", "experiment_validated"):
        op.add_column(
            "recommend_outcomes",
            sa.Column(
                "experiment_validated",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if not _has_column("recommend_outcomes", "validated_at"):
        op.add_column(
            "recommend_outcomes",
            sa.Column("validated_at", sa.DateTime(), nullable=True),
        )
    if not _has_column("recommend_outcomes", "validation_summary"):
        op.add_column(
            "recommend_outcomes",
            sa.Column("validation_summary", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    if _has_column("recommend_outcomes", "validation_summary"):
        op.drop_column("recommend_outcomes", "validation_summary")
    if _has_column("recommend_outcomes", "validated_at"):
        op.drop_column("recommend_outcomes", "validated_at")
    if _has_column("recommend_outcomes", "experiment_validated"):
        op.drop_column("recommend_outcomes", "experiment_validated")

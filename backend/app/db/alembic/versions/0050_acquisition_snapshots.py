"""Add acquisition_snapshots table (D-3: trend persistence).

Revision ID: 0050
Revises: 0049
Create Date: 2026-10-11

下载成功率趋势快照持久化，替代纯内存存储。
可回滚：drop table。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "acquisition_snapshots"


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_TABLE):
        # 0001_baseline 已用 create_all 从当前 models 建好该表（全新库），幂等跳过；
        # 既有库（0001 早于该表加入）则正常补建。
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("ts", sa.Float, nullable=False, index=True),
        sa.Column("project_id", sa.String(64), nullable=True, index=True),
        sa.Column("stats_json", sa.Text, nullable=False),
        sa.Column("success_rate", sa.Float, nullable=False),
    )


def downgrade() -> None:
    op.drop_table(_TABLE)

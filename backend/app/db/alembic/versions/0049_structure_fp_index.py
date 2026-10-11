"""Add fingerprint prefix index for fast structure search (S-3/P-2).

Revision ID: 0049
Revises: 0048
Create Date: 2026-10-11

指纹 hex 前 16 字符建索引，SQL 层预过滤，Python 只算候选。
可回滚：drop index。
"""
from __future__ import annotations

from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "structure_fingerprints"


def upgrade() -> None:
    # SQLite 不支持函数索引，用生成列或应用层处理。
    # 这里加普通索引在 fingerprint_hex 上，前缀查询可用 LIKE 'abc%' 走索引。
    op.create_index(
        "ix_structure_fingerprints_fp_hex",
        _TABLE,
        ["fingerprint_hex"],
    )


def downgrade() -> None:
    op.drop_index("ix_structure_fingerprints_fp_hex", table_name=_TABLE)

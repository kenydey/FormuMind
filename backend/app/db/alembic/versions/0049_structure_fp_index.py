"""Add fingerprint prefix index for fast structure search (S-3/P-2).

Revision ID: 0049
Revises: 0048
Create Date: 2026-10-11

指纹 hex 前 16 字符建索引，SQL 层预过滤，Python 只算候选。
可回滚：drop index。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "structure_fingerprints"


_INDEX = "ix_structure_fingerprints_fp_hex"


def _has_index() -> bool:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table(_TABLE):
        return False
    return any(ix["name"] == _INDEX for ix in insp.get_indexes(_TABLE))


def upgrade() -> None:
    # SQLite 不支持函数索引，用生成列或应用层处理。
    # 这里加普通索引在 fingerprint_hex 上，前缀查询可用 LIKE 'abc%' 走索引。
    # 幂等：表不存在（旧库 0048 之前未建）则无事可做；索引已在（0001 的 create_all 从当前 models 建出）则跳过。
    if not sa.inspect(op.get_bind()).has_table(_TABLE) or _has_index():
        return
    op.create_index(_INDEX, _TABLE, ["fingerprint_hex"])


def downgrade() -> None:
    if _has_index():
        op.drop_index(_INDEX, table_name=_TABLE)

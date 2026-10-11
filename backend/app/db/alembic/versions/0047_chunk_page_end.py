"""Add ``page_end`` to document_chunks (PageIndex 借鉴 A5: 页码范围引用).

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-11

跨页 chunk 的末页（NULL = 单页，与 page_no 相同）。引用显示 "pp. 5-7" 更诚实。
历史数据由 reindex_changed() 回填，无 backfill 压力。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "document_chunks"
_COL = "page_end"


def _has_table(table: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table)


def _has_column(table: str, column: str) -> bool:
    # A database that never had the table (a minimal legacy schema stamped at an earlier revision) has nothing to
    # alter: inspector.get_columns() raises NoSuchTableError for it, which used to abort `upgrade head`.
    if not _has_table(table):
        return False
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    if not _has_table(_TABLE):
        return
    if not _has_column(_TABLE, _COL):
        op.add_column(
            _TABLE,
            sa.Column(_COL, sa.Integer(), nullable=True, default=None),
        )


def downgrade() -> None:
    if _has_column(_TABLE, _COL):
        op.drop_column(_TABLE, _COL)

"""Add Hansen solubility parameters to materials.

Revision ID: 0046
Revises: 0045
Create Date: 2026-10-08

v20-2: Hansen 溶解度参数 (δd, δp, δh)，单位 MPa^0.5。
用于 structural_score 的化学相似度计算。NULL = 未知（fail-open）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "materials"
_COLUMNS = ["hansen_d", "hansen_p", "hansen_h"]


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    for col in _COLUMNS:
        if not _has_column(_TABLE, col):
            op.add_column(
                _TABLE,
                sa.Column(col, sa.Float(), nullable=True),
            )


def downgrade() -> None:
    for col in _COLUMNS:
        if _has_column(_TABLE, col):
            op.drop_column(_TABLE, col)

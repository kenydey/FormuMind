"""Add ``domain`` to campaigns (H-5: workbench domain hardcode fix).

Revision ID: 0046
Revises: 0045
Create Date: 2026-10-09

``requirement_from_campaign`` hardcoded ``ProductDomain.anticorrosion_coating``,
so non-anticorrosion campaigns got wrong objectives/levers. The domain is now
stored on the campaign at creation time and read back when rebuilding.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "campaigns"
_COL = "domain"


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
            sa.Column(_COL, sa.String(64), nullable=True, default=None),
        )
    # Backfill existing rows with the historical hardcoded default
    op.execute(
        sa.text(f"UPDATE {_TABLE} SET {_COL} = 'anticorrosion_coating' WHERE {_COL} IS NULL")
    )


def downgrade() -> None:
    if _has_column(_TABLE, _COL):
        op.drop_column(_TABLE, _COL)

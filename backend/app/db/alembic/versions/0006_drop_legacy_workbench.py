"""Drop the legacy ``experiment_records`` table.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-26

The pre-Alembic application kept a legacy ``experiment_records`` table that
is superseded by ``experiments``. Fresh databases never create it (the 0001
baseline only builds ORM tables), so the drop is guarded by a table-existence
check and is a silent no-op there.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Drop ``experiment_records`` if it exists; no-op on fresh databases."""
    inspector = sa.inspect(op.get_bind())
    if "experiment_records" in inspector.get_table_names():
        op.drop_table("experiment_records")


def downgrade() -> None:
    """No-op: the legacy table's original data is gone once dropped.

    Recreating an empty legacy table would serve no purpose and risk masking
    data-loss expectations, so the downgrade deliberately does nothing.
    """

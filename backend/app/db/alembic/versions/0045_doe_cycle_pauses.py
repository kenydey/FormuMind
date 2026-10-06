"""Add ``doe_cycle_pauses`` (the closed-loop pause flag, out of Redis).

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-05

The pause flag of a campaign's closed loop used to be a Redis key with a 24 h TTL: a development or
eager-mode install (no Redis) could not pause at all, an unreachable Redis read as "not paused", and a
pause set on Friday silently lapsed on Saturday. It is one row per campaign now, shared by the API and
the workers through the database they already share; see ``models.DOECyclePauseRow``.

``0001_baseline`` builds a fresh database with ``create_all`` from the *current* models, so a fresh
database already has the table; both directions check first and are safe to repeat. Pauses held in
Redis by an older build are not carried over - they were set to lapse within a day anyway.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "doe_cycle_pauses"


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table(_TABLE):
        return
    # Partial / synthetic legacy schemas may not have the campaigns table yet; the foreign key is then
    # left out rather than failing the whole upgrade (the application only writes rows for real campaigns).
    parent = [sa.ForeignKey("campaigns.id", ondelete="CASCADE")] if _has_table("campaigns") else []
    op.create_table(
        _TABLE,
        sa.Column("campaign_id", sa.Integer(), *parent, primary_key=True),
        sa.Column("paused_at", sa.DateTime(), nullable=False),
        sa.Column("paused_until", sa.DateTime(), nullable=True),
        sa.Column("lapsed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    if _has_table(_TABLE):
        op.drop_table(_TABLE)

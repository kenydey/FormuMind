"""Baseline migration: create all ORM tables.

Revision ID: 0001
Revises:
Create Date: 2026-07-26

Captures the pre-Alembic schema (previously managed by
``Base.metadata.create_all`` in ``app.db.database.make_engine``) as the
Alembic baseline. ``upgrade`` creates every table declared on
``app.db.models.Base``; ``downgrade`` drops them all.
"""
from __future__ import annotations

from alembic import op

from app.db.models import Base

revision: str = "0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create all tables declared on the ORM metadata."""
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    """Drop all tables declared on the ORM metadata."""
    Base.metadata.drop_all(op.get_bind())

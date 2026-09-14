"""Baseline migration: create all ORM tables.

Revision ID: 0001
Revises:
Create Date: 2026-07-26

Captures the pre-Alembic schema (previously managed by
``Base.metadata.create_all`` in ``app.db.database.make_engine``) as the
Alembic baseline. ``upgrade`` creates every table declared on
``app.db.models.Base``; ``downgrade`` drops them all.

Uses the process-wide schema DDL lock from ``app.db.database`` so concurrent
``make_engine`` / eager-worker ``create_all`` calls cannot race SQLite's
checkfirst TOCTOU (``table … already exists``).
"""
from __future__ import annotations

from alembic import op

from app.db.database import create_all_metadata, drop_all_metadata

revision: str = "0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create all tables declared on the ORM metadata."""
    create_all_metadata(op.get_bind())


def downgrade() -> None:
    """Drop all tables declared on the ORM metadata."""
    drop_all_metadata(op.get_bind())

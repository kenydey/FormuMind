"""Alembic 0025 — wiki_pages (LLM Wiki metadata).

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-09

Idempotent: table created only when absent.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "wiki_pages"):
        return
    op.create_table(
        "wiki_pages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="material"),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("norm_key", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("content_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("source_ids", sa.JSON(), nullable=True),
        sa.Column("flags", sa.JSON(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_wiki_pages_path", "wiki_pages", ["path"], unique=True)
    op.create_index("ix_wiki_pages_kind", "wiki_pages", ["kind"])
    op.create_index("ix_wiki_pages_entity_id", "wiki_pages", ["entity_id"])
    op.create_index("ix_wiki_pages_norm_key", "wiki_pages", ["norm_key"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "wiki_pages"):
        op.drop_index("ix_wiki_pages_norm_key", table_name="wiki_pages")
        op.drop_index("ix_wiki_pages_entity_id", table_name="wiki_pages")
        op.drop_index("ix_wiki_pages_kind", table_name="wiki_pages")
        op.drop_index("ix_wiki_pages_path", table_name="wiki_pages")
        op.drop_table("wiki_pages")

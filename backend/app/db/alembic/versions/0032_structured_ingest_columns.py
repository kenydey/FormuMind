"""Add layout-provenance columns to ``document_chunks`` + extraction tables.

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-28

Phase 0 (ragflow/kotaemon eval): structured-ingest foundation.
- ``document_chunks.bbox`` — normalized [xmin,ymin,xmax,ymax] page-fraction
  bbox (JSON); ``document_chunks.block_type`` — text|table|formula|figure|code.
- New tables ``extraction_tables`` / ``extraction_formulas`` (empty until a
  layout-aware parser fills them in Phase 1).

Idempotent: mirrors the 0009 pattern — fresh databases already get everything
from the 0001 baseline / model create_all, so every DDL is guarded by a
sqlalchemy.inspect existence check.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_NEW_CHUNK_COLUMNS: tuple[sa.Column, ...] = (
    sa.Column("bbox", sa.JSON(), nullable=True),
    sa.Column("block_type", sa.String(16), nullable=True),
)


def _existing_columns(table: str) -> set[str]:
    """Return the column names currently present on ``table`` (empty if absent)."""
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def _existing_tables() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return set(inspector.get_table_names())


def upgrade() -> None:
    """Add layout columns and extraction tables, skipping what already exists."""
    existing = _existing_columns("document_chunks")
    for column in _NEW_CHUNK_COLUMNS:
        if column.name not in existing:
            op.add_column("document_chunks", column)

    tables = _existing_tables()
    if "extraction_tables" not in tables:
        op.create_table(
            "extraction_tables",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("source_id", sa.String(36), index=True),
            sa.Column("page_no", sa.Integer, nullable=True),
            sa.Column("bbox", sa.JSON(), nullable=True),
            sa.Column("caption", sa.String(500), nullable=True),
            sa.Column("markdown_text", sa.Text, nullable=False, server_default=""),
            sa.Column("n_rows", sa.Integer, nullable=True),
            sa.Column("n_cols", sa.Integer, nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=True),
        )
    if "extraction_formulas" not in tables:
        op.create_table(
            "extraction_formulas",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("source_id", sa.String(36), index=True),
            sa.Column("page_no", sa.Integer, nullable=True),
            sa.Column("bbox", sa.JSON(), nullable=True),
            sa.Column("latex", sa.Text, nullable=False, server_default=""),
            sa.Column("formula_no", sa.String(40), nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=True),
        )


def downgrade() -> None:
    """No-op: adding nullable columns/tables is irreversible but harmless."""

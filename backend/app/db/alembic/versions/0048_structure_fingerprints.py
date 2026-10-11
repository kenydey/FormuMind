"""Add ``structure_fingerprints`` table (P1-3: RDKit 结构索引).

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-11

化学结构指纹索引：SMILES → Morgan 指纹 hex，支持 Tanimoto 相似搜索。
关联 document_chunks（chunk_id），非第二套检索引擎。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "structure_fingerprints"


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    return table in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    if _has_table(_TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("chunk_id", sa.String(36), index=True, nullable=False),
        sa.Column("source_id", sa.String(36), index=True, nullable=False),
        sa.Column("smiles", sa.String(2000), nullable=False, default=""),
        sa.Column("fingerprint_hex", sa.Text, nullable=False, default=""),
        sa.Column("origin", sa.String(32), nullable=False, default=""),
    )
    op.create_index("ix_struct_fp_chunk", _TABLE, ["chunk_id"])
    op.create_index("ix_struct_fp_source", _TABLE, ["source_id"])


def downgrade() -> None:
    if _has_table(_TABLE):
        op.drop_table(_TABLE)

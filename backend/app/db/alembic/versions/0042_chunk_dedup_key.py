"""Add indexed ``dedup_key`` to ``document_chunks`` (P2: L1 dedup scan).

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-02

P2: L1 精确去重每次 ingest 全表扫描 document_chunks（text, heading_path,
page_no 三列逐行哈希）。本迁移加 ``dedup_key`` 列（sha256 hex，64 字符）
+ 索引；写入时由 ``ChunkStore.replace_for_source_in`` 统一计算；
``kb_dedup._l1_exact`` 改为 ``WHERE dedup_key IN (...)`` 索引查找。

回填：对存量行按同样算法计算 key。列可空 —— 极端情况下回填失败的行
保持 NULL，_l1_exact 对其回退为不去重（不阻塞 ingest）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    # Same guard as 0039: partial / synthetic legacy schemas (e.g. the 0032
    # fixture DB) have no document_chunks table, and inspecting columns of a
    # missing table raises NoSuchTableError, aborting `upgrade head`.
    if not _has_table("document_chunks"):
        return
    if not _has_column("document_chunks", "dedup_key"):
        op.add_column(
            "document_chunks",
            sa.Column("dedup_key", sa.String(64), nullable=True),
        )
    # 索引幂等：已存在则跳过。
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = {ix["name"] for ix in insp.get_indexes("document_chunks")}
    if "ix_document_chunks_dedup_key" not in existing:
        op.create_index(
            "ix_document_chunks_dedup_key", "document_chunks", ["dedup_key"]
        )
    # 回填存量行。
    try:
        from app.services.kb_dedup import chunk_dedup_key

        conn = op.get_bind()
        rows = conn.execute(
            sa.text(
                "SELECT id, text, heading_path, page_no FROM document_chunks "
                "WHERE dedup_key IS NULL"
            )
        ).all()
        for rid, text, heading_path, page_no in rows:
            key = chunk_dedup_key(text, heading_path, page_no)
            conn.execute(
                sa.text(
                    "UPDATE document_chunks SET dedup_key = :k WHERE id = :i"
                ),
                {"k": key, "i": rid},
            )
    except Exception as exc:  # noqa: BLE001 - 回填失败不阻塞迁移
        import logging

        logging.getLogger(__name__).warning(
            "0042 dedup_key backfill failed (fail-open): %s", exc
        )


def downgrade() -> None:
    if not _has_table("document_chunks"):
        return
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = {ix["name"] for ix in insp.get_indexes("document_chunks")}
    if "ix_document_chunks_dedup_key" in existing:
        op.drop_index("ix_document_chunks_dedup_key", table_name="document_chunks")
    if _has_column("document_chunks", "dedup_key"):
        op.drop_column("document_chunks", "dedup_key")

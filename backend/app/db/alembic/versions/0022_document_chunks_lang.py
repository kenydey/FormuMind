"""add lang to document_chunks (bilingual routing)

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-04 16:05:00.000000

背景: 双语资料分流(2026-09-04 方案)需要按语言过滤 document_chunks
("zh" | "en" | None)。幂等: 旧库无列时 ADD + INDEX; 已有列/新库跳过。
生产 db(create_all 体系, 无 alembic_version 表)由一次性脚本手工
ALTER——此迁移服务未来走 alembic 的库。
"""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "document_chunks" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("document_chunks")}
    if "lang" not in columns:
        op.add_column("document_chunks", sa.Column("lang", sa.String(8), nullable=True))
        op.create_index("ix_document_chunks_lang", "document_chunks", ["lang"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "document_chunks" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("document_chunks")}
    if "lang" in columns:
        op.drop_index("ix_document_chunks_lang", table_name="document_chunks")
        op.drop_column("document_chunks", "lang")

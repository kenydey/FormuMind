"""v7: KG 链接表复合索引 (src/dst × link_type)。

KG 图谱查询热点是 (src_entity_id, link_type) 复合过滤（entity_store.py 9 处、
kg_feedback.py、kg.py counts），单列索引下需回表过滤。加两个复合索引。
"""

from __future__ import annotations

from alembic import op

revision = "0018_kg_link_composite_index"
down_revision = "0017_owner_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    table = "kb_entity_links"
    if table not in inspector.get_table_names():
        return
    existing = {ix["name"] for ix in inspector.get_indexes(table)}
    if "ix_kb_link_src_type" not in existing:
        op.create_index("ix_kb_link_src_type", table, ["src_entity_id", "link_type"])
    if "ix_kb_link_dst_type" not in existing:
        op.create_index("ix_kb_link_dst_type", table, ["dst_entity_id", "link_type"])


def downgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    table = "kb_entity_links"
    if table not in inspector.get_table_names():
        return
    existing = {ix["name"] for ix in inspector.get_indexes(table)}
    if "ix_kb_link_src_type" in existing:
        op.drop_index("ix_kb_link_src_type", table_name=table)
    if "ix_kb_link_dst_type" in existing:
        op.drop_index("ix_kb_link_dst_type", table_name=table)

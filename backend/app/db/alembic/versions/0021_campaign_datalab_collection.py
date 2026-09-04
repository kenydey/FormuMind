"""add datalab_collection_id to campaigns

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-04 10:30:00.000000

背景：2026-09 上游在 models.Campaign 新增 datalab_collection_id（P1
DataLab collection 集成），仅靠 create_all 覆盖新库；既有库经 alembic
升级时缺此列，ORM insert 会报 ``no column named datalab_collection_id``。
此处补迁移（幂等：旧库无列时 ADD，已有列/新库跳过）。
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "campaigns" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("campaigns")}
    if "datalab_collection_id" not in columns:
        op.add_column(
            "campaigns",
            sa.Column(
                "datalab_collection_id",
                sa.String(length=96),
                nullable=True,
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "campaigns" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("campaigns")}
    if "datalab_collection_id" in columns:
        op.drop_column("campaigns", "datalab_collection_id")

"""add kg_formulation_links table

Revision ID: 0020
Revises: 0019_add_task_id_to_experiments
Create Date: 2026-09-03 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019_add_task_id_to_experiments"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # 0001_baseline 用 Base.metadata.create_all() 动态建全表：models.py 若已含
    # kg_formulation_links（2026-09-03 同批加入），全新库在 0001 就已建好该表，
    # 此处必须幂等跳过；既有库（0001 早于该表加入）则正常补建。
    bind = op.get_bind()
    if sa.inspect(bind).has_table("kg_formulation_links"):
        return
    op.create_table(
        "kg_formulation_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("experiment_id", sa.Integer(), sa.ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("entity_id", sa.String(64), sa.ForeignKey("kb_entities.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("role", sa.String(60), default=""),
        sa.Column("weight_pct", sa.Float(), nullable=True),
        sa.Column("link_type", sa.String(32), default="contains"),
        sa.Column("project_id", sa.String(64), default="", index=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("experiment_id", "entity_id", "role", name="uq_kg_formulation_link"),
    )

def downgrade() -> None:
    op.drop_table("kg_formulation_links")

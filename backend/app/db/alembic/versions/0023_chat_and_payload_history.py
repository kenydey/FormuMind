"""add chat_sessions / chat_messages / project_payload_history

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-05 09:00:00.000000

背景: 会话入库项目数据库(2026-09-05 方案 v2)——chat_sessions/chat_messages
使对话成为项目数据(SQLite 权威, Redis 仅热缓存); project_payload_history
为 payload 全量覆盖提供版本快照与回滚。幂等: 表已存在则跳过。
生产 db(create_all 体系, 无 alembic_version 表)由 Base.metadata.create_all
自动建表; 此迁移服务未来走 alembic 的库。
"""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    inspector = sa.inspect(bind)
    return name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "chat_sessions"):
        op.create_table(
            "chat_sessions",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("project_id", sa.String(36), nullable=True),
            sa.Column("title", sa.String(255), nullable=False, server_default=""),
            sa.Column("context", sa.JSON(), nullable=True),
            sa.Column("has_context", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_chat_sessions_project_id", "chat_sessions", ["project_id"])
    if not _has_table(bind, "chat_messages"):
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("session_id", sa.String(64), nullable=False),
            sa.Column("project_id", sa.String(36), nullable=True),
            sa.Column("role", sa.String(16), nullable=False, server_default="user"),
            sa.Column("content", sa.Text(), nullable=False, server_default=""),
            sa.Column("meta_json", sa.JSON(), nullable=True),
            sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])
        op.create_index("ix_chat_messages_project_id", "chat_messages", ["project_id"])
    if not _has_table(bind, "project_payload_history"):
        op.create_table(
            "project_payload_history",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("project_id", sa.String(36), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("cause", sa.String(64), nullable=False, server_default="update"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index(
            "ix_project_payload_history_project_id", "project_payload_history", ["project_id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    for name in ("project_payload_history", "chat_messages", "chat_sessions"):
        if _has_table(bind, name):
            op.drop_table(name)

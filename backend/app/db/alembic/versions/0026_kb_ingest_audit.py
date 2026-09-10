"""Alembic 0026 — kb_ingest_audit (P0 domain-profile ingest gate).

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-10

Idempotent: table created only when absent. Not cascade-deleted with sources.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "kb_ingest_audit"):
        return
    op.create_table(
        "kb_ingest_audit",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("project_id", sa.String(length=64), nullable=True),
        sa.Column("domain", sa.String(length=64), nullable=True),
        sa.Column("query_fingerprint", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("evidence_id", sa.String(length=512), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=16), nullable=False, server_default="skip"),
        sa.Column("reason", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("domain_match", sa.String(length=16), nullable=True),
    )
    op.create_index("ix_kb_ingest_audit_created_at", "kb_ingest_audit", ["created_at"])
    op.create_index("ix_kb_ingest_audit_project_id", "kb_ingest_audit", ["project_id"])
    op.create_index("ix_kb_ingest_audit_domain", "kb_ingest_audit", ["domain"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "kb_ingest_audit"):
        return
    op.drop_index("ix_kb_ingest_audit_domain", table_name="kb_ingest_audit")
    op.drop_index("ix_kb_ingest_audit_project_id", table_name="kb_ingest_audit")
    op.drop_index("ix_kb_ingest_audit_created_at", table_name="kb_ingest_audit")
    op.drop_table("kb_ingest_audit")

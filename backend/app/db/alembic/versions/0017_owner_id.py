"""P2 owner_id pre埋 — nullable, index, 无历史数据影响."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0017_owner_id"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    for table in ("campaigns", "experiments", "task_outbox"):
        if table not in inspector.get_table_names():
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        if "owner_id" in existing:
            continue
        # SQLite: ADD COLUMN is cheap and nullable columns need no backfill.
        # Postgres: same. Use batch for SQLite FK quirks.
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("owner_id", sa.String(length=64), nullable=True))
            batch.create_index(f"ix_{table}_owner_id", ["owner_id"])


def downgrade() -> None:
    for table in ("campaigns", "experiments", "task_outbox"):
        with op.batch_alter_table(table) as batch:
            try:
                batch.drop_index(f"ix_{table}_owner_id")
            except Exception:
                pass
            batch.drop_column("owner_id")

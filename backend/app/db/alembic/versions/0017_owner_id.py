"""P2 owner_id pre埋 — nullable, index, 无历史数据影响."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0017_owner_id"
down_revision = "0016_doe_plans_round"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("campaigns", "experiments", "task_outbox"):
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

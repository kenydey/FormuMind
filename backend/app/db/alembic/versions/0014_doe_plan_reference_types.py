"""Fix ``doe_plans`` reference column types and add the foreign keys.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-28

``experiment_id`` and ``campaign_id`` were declared ``String(36)`` while
``experiments.id`` and ``campaigns.id`` are autoincrement integers, so neither
column could ever join its parent — a plan's experiment and campaign were
unreachable by query. Nothing ever populated them, which is why it went
unnoticed; the integrity check added in 0012's wake had to report the
reference as structurally unjoinable rather than merely drifted.

Both columns are converted to ``Integer`` with real foreign keys. Any value
that is not an integer is discarded first: it could not have referred to a
real row anyway, since no integer primary key can equal a UUID string.

SQLite cannot ALTER a column type in place, so this runs through
``batch_alter_table``, which rebuilds the table.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

TABLE = "doe_plans"


def _column_type(bind, column: str) -> str:
    inspector = sa.inspect(bind)
    if TABLE not in inspector.get_table_names():
        return ""
    for col in inspector.get_columns(TABLE):
        if col["name"] == column:
            return str(col["type"]).upper()
    return ""


def _not_an_integer(bind, column: str) -> str:
    """SQL predicate matching values that are not a plain integer string.

    This pass is what makes the conversion safe on PostgreSQL, where the
    ``::integer`` cast below raises on a non-numeric value. On SQLite the
    orphan sweep that follows would catch the same rows anyway, so treat this
    as the dialect-portable half of the cleanup rather than a SQLite guard.

    A naive ``CAST(x AS INTEGER) = 0`` test would not do: SQLite parses the
    leading digits of a UUID, so ``'8b4f-...'`` casts to ``8``. Compare the
    round trip through INTEGER against the original instead — only a pure
    integer survives it.
    """
    if bind.dialect.name == "postgresql":
        return f"{column} !~ '^[0-9]+$'"
    return f"CAST(CAST({column} AS INTEGER) AS TEXT) <> CAST({column} AS TEXT)"


def upgrade() -> None:
    bind = op.get_bind()
    if TABLE not in set(sa.inspect(bind).get_table_names()):
        return
    # Already integer (a fresh database built from the current ORM) — nothing
    # to convert, and re-running batch_alter_table would drop the constraints.
    if "INT" in _column_type(bind, "experiment_id"):
        return

    # Two cleanup passes, in this order. Both run while the columns are still
    # TEXT, which is what makes the second one correct: a non-numeric string
    # never compares equal to an integer primary key, so it is swept as an
    # orphan before any cast can reinterpret its leading digits.
    for column, parent in (("experiment_id", "experiments"), ("campaign_id", "campaigns")):
        bind.execute(
            sa.text(
                f"UPDATE {TABLE} SET {column} = NULL WHERE {column} IS NOT NULL "
                f"AND {_not_an_integer(bind, column)}"
            )
        )
        # Drop every value with no parent row. SQLite rebuilds a table without
        # re-checking foreign keys, so an orphan left here would be written
        # straight through the new constraint and only surface much later as a
        # PRAGMA foreign_key_check failure.
        bind.execute(
            sa.text(
                f"UPDATE {TABLE} SET {column} = NULL WHERE {column} IS NOT NULL "
                f"AND {column} NOT IN (SELECT id FROM {parent})"
            )
        )

    with op.batch_alter_table(TABLE) as batch_op:
        batch_op.alter_column(
            "experiment_id", existing_type=sa.String(36), type_=sa.Integer(),
            existing_nullable=True, postgresql_using="experiment_id::integer",
        )
        batch_op.alter_column(
            "campaign_id", existing_type=sa.String(36), type_=sa.Integer(),
            existing_nullable=True, postgresql_using="campaign_id::integer",
        )
        batch_op.create_foreign_key(
            "fk_doe_plans_experiment_id", "experiments", ["experiment_id"], ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_doe_plans_campaign_id", "campaigns", ["campaign_id"], ["id"],
            ondelete="SET NULL",
        )


def _doe_plans_without_foreign_keys() -> sa.Table:
    """The ``doe_plans`` shape to rebuild on downgrade — no foreign keys.

    ``batch_alter_table`` normally reflects the existing table, but SQLite does
    not report a name for a reflected foreign key, so ``drop_constraint`` by
    name cannot find the ones ``upgrade`` created. Handing batch mode an
    explicit definition sidesteps reflection entirely: the rebuilt table is
    exactly what is described here, so simply omitting the foreign keys drops
    them. The flip side is that anything left out is also dropped, which is why
    the indexes migration 0008 created are restated.
    """
    table = sa.Table(
        TABLE,
        sa.MetaData(),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("experiment_id", sa.Integer, nullable=True),
        sa.Column("campaign_id", sa.Integer, nullable=True),
        sa.Column("design_type", sa.String(32), nullable=False),
        sa.Column("parameters", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    sa.Index("ix_doe_plans_experiment", table.c.experiment_id)
    sa.Index("ix_doe_plans_campaign", table.c.campaign_id)
    return table


def downgrade() -> None:
    """Revert the columns to String(36), dropping the foreign keys."""
    bind = op.get_bind()
    if TABLE not in set(sa.inspect(bind).get_table_names()):
        return
    with op.batch_alter_table(
        TABLE, copy_from=_doe_plans_without_foreign_keys()
    ) as batch_op:
        batch_op.alter_column(
            "experiment_id", existing_type=sa.Integer(), type_=sa.String(36),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "campaign_id", existing_type=sa.Integer(), type_=sa.String(36),
            existing_nullable=True,
        )

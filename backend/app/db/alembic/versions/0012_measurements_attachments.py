"""Add ``measurements`` and ``experiment_attachments``.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-28

Typed lab results (unit, test method, instrument, operator, spec window) and
the binding from an ingested QC report back to the experiment it reports on.

These are the first tables in the schema with real foreign keys: both parents
are local tables, never Datalab-authoritative, so the constraint can be
enforced rather than merely documented.

Idempotent: the table and each index are created only when absent.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_MEASUREMENTS = "measurements"
_ATTACHMENTS = "experiment_attachments"

_INDEXES = (
    (_MEASUREMENTS, "ix_measurements_experiment_id", ["experiment_id"], False),
    (_MEASUREMENTS, "ix_measurements_metric", ["metric"], False),
    (_MEASUREMENTS, "ix_measurements_source_document_id", ["source_document_id"], False),
    (_MEASUREMENTS, "ix_measurements_experiment_metric", ["experiment_id", "metric"], False),
    (_ATTACHMENTS, "ix_experiment_attachments_experiment_id", ["experiment_id"], False),
    (_ATTACHMENTS, "ix_experiment_attachments_source_document_id", ["source_document_id"], False),
)


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if _MEASUREMENTS not in tables:
        op.create_table(
            _MEASUREMENTS,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "experiment_id",
                sa.Integer,
                sa.ForeignKey("experiments.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("metric", sa.String(80), nullable=False),
            sa.Column("value", sa.Float, nullable=False),
            sa.Column("unit", sa.String(32), nullable=False, server_default=""),
            sa.Column("test_method", sa.String(80), nullable=False, server_default=""),
            sa.Column("instrument", sa.String(120), nullable=False, server_default=""),
            sa.Column("operator", sa.String(80), nullable=False, server_default=""),
            sa.Column("measured_at", sa.DateTime, nullable=True),
            sa.Column("spec_min", sa.Float, nullable=True),
            sa.Column("spec_max", sa.Float, nullable=True),
            sa.Column("passed", sa.Boolean, nullable=True),
            # SET NULL, not CASCADE: losing the report must not silently delete
            # the measurement it produced.
            sa.Column(
                "source_document_id",
                sa.String(36),
                sa.ForeignKey("source_documents.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("note", sa.Text, nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime, nullable=False),
        )

    if _ATTACHMENTS not in tables:
        op.create_table(
            _ATTACHMENTS,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "experiment_id",
                sa.Integer,
                sa.ForeignKey("experiments.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "source_document_id",
                sa.String(36),
                sa.ForeignKey("source_documents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("kind", sa.String(32), nullable=False, server_default="qc_report"),
            sa.Column("note", sa.Text, nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.UniqueConstraint(
                "experiment_id", "source_document_id", name="uq_experiment_attachment"
            ),
        )

    inspector = sa.inspect(bind)
    present = set(inspector.get_table_names())
    for table, name, columns, unique in _INDEXES:
        if table not in present:
            continue
        existing = {ix["name"] for ix in inspector.get_indexes(table)}
        if name not in existing:
            op.create_index(name, table, columns, unique=unique)


def downgrade() -> None:
    """Drop both tables."""
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if _ATTACHMENTS in tables:
        op.drop_table(_ATTACHMENTS)
    if _MEASUREMENTS in tables:
        op.drop_table(_MEASUREMENTS)

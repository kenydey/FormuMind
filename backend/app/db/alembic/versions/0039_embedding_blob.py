"""Add ``embedding_blob`` BLOB column to ``document_chunks`` (Phase C-1b).

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-01

Phase C (C-1): the JSON ``embedding`` column is the measured storage/latency
ceiling (Wave 4 finding). The new ``embedding_blob`` column stores the same
vector as float32 little-endian bytes (~4x smaller than the JSON text and
directly consumable by the faiss ANN index without a JSON parse).

Upgrade:
  1. Adds the nullable ``embedding_blob`` BLOB column (idempotent guard).
  2. Backfills it from ``embedding`` JSON for rows whose blob is still NULL
     (idempotent — a re-run only touches NULL rows; a fresh database created
     via ``Base.metadata.create_all`` already has the column so step 1 is
     skipped and step 2 finds no JSON rows).

Downgrade drops the column. The JSON ``embedding`` column is intentionally
kept: pre-C-1 readers (wiki embed, kb_index paths) still read it, and the
faiss reader falls back to JSON when the blob is missing.
"""

from __future__ import annotations

import json
import struct

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_BATCH = 500


def _to_blob(vec: list) -> bytes | None:
    try:
        vals = [float(x) for x in vec]
        if not vals:
            return None
        return struct.pack(f"<{len(vals)}f", *vals)
    except Exception:  # noqa: BLE001
        return None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "document_chunks" not in inspector.get_table_names():
        # Synthetic pre-chunk schemas (e.g. the 0032 test DB) have no such
        # table: nothing to add or backfill.
        return
    cols = [c["name"] for c in inspector.get_columns("document_chunks")]
    if "embedding_blob" not in cols:
        op.add_column(
            "document_chunks",
            sa.Column("embedding_blob", sa.LargeBinary(), nullable=True),
        )

    # Backfill in batches; only rows with a JSON vector and no blob yet.
    while True:
        rows = bind.execute(
            sa.text(
                "SELECT id, embedding FROM document_chunks "
                "WHERE embedding_blob IS NULL AND embedding IS NOT NULL "
                "LIMIT :lim"
            ),
            {"lim": _BATCH},
        ).fetchall()
        if not rows:
            break
        updates = []
        for row_id, emb_json in rows:
            try:
                vec = json.loads(emb_json) if isinstance(emb_json, str) else emb_json
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(vec, list) or not vec:
                continue
            blob = _to_blob(vec)
            if blob is not None:
                updates.append({"id": row_id, "blob": blob})
        if updates:
            bind.execute(
                sa.text(
                    "UPDATE document_chunks SET embedding_blob = :blob WHERE id = :id"
                ),
                updates,
            )
        if len(rows) < _BATCH:
            break


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "document_chunks" not in inspector.get_table_names():
        return
    cols = [c["name"] for c in inspector.get_columns("document_chunks")]
    if "embedding_blob" in cols:
        op.drop_column("document_chunks", "embedding_blob")

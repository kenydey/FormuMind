"""Add ``project_id`` isolation columns to ``doe_plans`` + ``wiki_pages``.

Revision ID: 0033
Revises: 0032
Create Date: 2026-09-29

Phase 0 (P0-1 / P0-2): close two project-isolation gaps.
- ``doe_plans.project_id`` — ``GET /doe/history`` returned every plan in the
  database when ``campaign_id`` was omitted; the column lets the endpoint go
  fail-closed per project like ``/doe/cycle-runs`` already does.
- ``wiki_pages.project_id`` — wiki pages were globally shared; chat's wiki
  evidence blend could surface another project's compiled knowledge.

Both columns are nullable (NULL = legacy / unscoped rows) so the upgrade is
non-breaking. Idempotent: every DDL is guarded by a sqlalchemy.inspect
existence check (mirrors the 0032 pattern).

Known limitation (documented, not fixed here): ``wiki_pages.path`` stays
unique, so two projects compiling the same path still share one row — the
read-side filter (``WikiStore.list_pages(project_id=...)``) stops the leak
for retrieval, but a same-path upsert from another project logs a warning
(see ``WikiStore.upsert_page``).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_NEW_COLUMNS: tuple[tuple[str, sa.Column, str], ...] = (
    # (table, column, index_name)
    (
        "doe_plans",
        sa.Column("project_id", sa.String(36), nullable=True),
        "ix_doe_plans_project",
    ),
    (
        "wiki_pages",
        sa.Column("project_id", sa.String(36), nullable=True),
        "ix_wiki_pages_project",
    ),
)


def _existing_columns(table: str) -> set[str]:
    """Return the column names currently present on ``table`` (empty if absent)."""
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def _existing_indexes(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {ix["name"] for ix in inspector.get_indexes(table)}


def _existing_tables() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return set(inspector.get_table_names())


def _backfill_project_ids() -> None:
    """One-time attribution for legacy NULL rows.

    Idempotent: only rows with ``project_id IS NULL`` are touched, so
    re-running the upgrade is a no-op. Rows that cannot be attributed to
    exactly one project keep NULL (legacy / unscoped) — the read side
    treats NULL as unscoped, never as belonging to a project.
    """
    import json

    bind = op.get_bind()
    tables = _existing_tables()
    cols = {t: _existing_columns(t) for t in tables}

    # --- doe_plans: via campaign, then experiment -------------------------
    if "doe_plans" in tables and "project_id" in cols["doe_plans"]:
        if "campaigns" in tables and "project_id" in cols["campaigns"]:
            bind.execute(
                sa.text(
                    "UPDATE doe_plans SET project_id = ("
                    "SELECT project_id FROM campaigns "
                    "WHERE campaigns.id = doe_plans.campaign_id) "
                    "WHERE project_id IS NULL AND campaign_id IS NOT NULL "
                    "AND (SELECT project_id FROM campaigns "
                    "WHERE campaigns.id = doe_plans.campaign_id) IS NOT NULL "
                    "AND (SELECT project_id FROM campaigns "
                    "WHERE campaigns.id = doe_plans.campaign_id) != ''"
                )
            )
        if "experiments" in tables and "project_id" in cols["experiments"]:
            bind.execute(
                sa.text(
                    "UPDATE doe_plans SET project_id = ("
                    "SELECT project_id FROM experiments "
                    "WHERE experiments.id = doe_plans.experiment_id) "
                    "WHERE project_id IS NULL AND experiment_id IS NOT NULL "
                    "AND (SELECT project_id FROM experiments "
                    "WHERE experiments.id = doe_plans.experiment_id) IS NOT NULL "
                    "AND (SELECT project_id FROM experiments "
                    "WHERE experiments.id = doe_plans.experiment_id) != ''"
                )
            )

    # --- wiki_pages: via source_ids -> source_documents.project_id --------
    if (
        "wiki_pages" in tables
        and "project_id" in cols["wiki_pages"]
        and "source_documents" in tables
        and "project_id" in cols["source_documents"]
    ):
        src_rows = bind.execute(
            sa.text("SELECT id, project_id FROM source_documents")
        ).all()
        src_project = {
            str(r[0]): str(r[1]) for r in src_rows if r[1] and str(r[1]).strip()
        }
        if src_project:
            null_rows = bind.execute(
                sa.text(
                    "SELECT id, source_ids FROM wiki_pages "
                    "WHERE project_id IS NULL"
                )
            ).all()
            for wid, sids_raw in null_rows:
                try:
                    sids = json.loads(sids_raw) if sids_raw else []
                except Exception:
                    sids = []
                if not isinstance(sids, list):
                    continue
                pids = {src_project[s] for s in sids if s in src_project}
                if len(pids) == 1:
                    bind.execute(
                        sa.text(
                            "UPDATE wiki_pages SET project_id = :pid "
                            "WHERE id = :wid"
                        ),
                        {"pid": next(iter(pids)), "wid": wid},
                    )


def upgrade() -> None:
    """Add project_id columns + indexes, skipping what already exists."""
    for table, column, index_name in _NEW_COLUMNS:
        if column.name not in _existing_columns(table):
            op.add_column(table, column)
        if index_name not in _existing_indexes(table):
            op.create_index(index_name, table, [column.name])
    _backfill_project_ids()


def downgrade() -> None:
    """Drop the project_id columns + indexes added here (guarded)."""
    for table, column, index_name in _NEW_COLUMNS:
        if index_name in _existing_indexes(table):
            op.drop_index(index_name, table_name=table)
        if column.name in _existing_columns(table):
            op.drop_column(table, column.name)

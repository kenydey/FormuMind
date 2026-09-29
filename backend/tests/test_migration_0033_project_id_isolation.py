"""Migration 0033: ``project_id`` isolation columns on ``doe_plans``/``wiki_pages``.

Covers four guarantees:

1. A legacy database (pre-0033 schema, stamped at 0032) gains both columns
   and indexes after ``alembic upgrade head``, and the one-time backfill
   attributes rows that map to exactly one project.
2. Ambiguous / unattributable legacy rows keep ``project_id IS NULL``
   (unscoped, never leaked into a project).
3. ``upgrade head`` on an empty DB is a silent no-op for 0033 (idempotent;
   also safe to run twice).
4. ``downgrade`` to 0032 drops the columns and indexes again.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from tests.alembic_helpers import (
    ALEMBIC_INI,
    make_config,
    run_downgrade,
    run_upgrade,
)


def _column_names(db_url: str, table: str) -> set[str]:
    engine = create_engine(db_url)
    try:
        return {c["name"] for c in inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def _index_names(db_url: str, table: str) -> set[str]:
    engine = create_engine(db_url)
    try:
        return {ix["name"] for ix in inspect(engine).get_indexes(table)}
    finally:
        engine.dispose()


def _fetch_all(db_url: str, sql: str) -> list[tuple]:
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            return [tuple(row) for row in conn.execute(text(sql))]
    finally:
        engine.dispose()


def _exec(db_url: str, sql: str) -> None:
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(sql))
    finally:
        engine.dispose()


@pytest.fixture()
def tmp_db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"sqlite:///{tmp_path}/m0033_test.db"
    monkeypatch.setenv("FORMUMIND_DB_URL", url)
    return url


def _create_pre_0033_schema(db_url: str) -> None:
    """Hand-built pre-0033 shapes: no project_id on doe_plans / wiki_pages."""
    engine = create_engine(db_url)
    statements = [
        """
        CREATE TABLE campaigns (
            id INTEGER PRIMARY KEY,
            name VARCHAR(255),
            project_id VARCHAR(36)
        )
        """,
        """
        CREATE TABLE experiments (
            id INTEGER PRIMARY KEY,
            project_id VARCHAR(36)
        )
        """,
        """
        CREATE TABLE source_documents (
            id VARCHAR(36) PRIMARY KEY,
            project_id VARCHAR(36)
        )
        """,
        """
        CREATE TABLE doe_plans (
            id VARCHAR(36) PRIMARY KEY,
            experiment_id INTEGER,
            campaign_id INTEGER,
            design_type VARCHAR(32),
            parameters JSON,
            round INTEGER,
            created_at DATETIME
        )
        """,
        """
        CREATE TABLE wiki_pages (
            id VARCHAR(36) PRIMARY KEY,
            path VARCHAR(512),
            kind VARCHAR(32),
            entity_id VARCHAR(64),
            title VARCHAR(512),
            norm_key VARCHAR(200),
            content_hash VARCHAR(64),
            source_ids JSON,
            flags JSON,
            revision INTEGER,
            updated_at DATETIME,
            created_at DATETIME
        )
        """,
    ]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    engine.dispose()


def _seed_legacy_rows(db_url: str) -> None:
    _exec(
        db_url,
        "INSERT INTO campaigns (id, name, project_id) VALUES"
        " (1, 'camp-a', 'proj-A'), (2, 'camp-b', 'proj-B')",
    )
    _exec(
        db_url,
        "INSERT INTO experiments (id, project_id) VALUES (10, 'proj-A')",
    )
    _exec(
        db_url,
        "INSERT INTO source_documents (id, project_id) VALUES"
        " ('src-a1', 'proj-A'), ('src-a2', 'proj-A'), ('src-b1', 'proj-B')",
    )
    _exec(
        db_url,
        "INSERT INTO doe_plans (id, experiment_id, campaign_id, design_type,"
        " parameters, round, created_at) VALUES"
        " ('plan-c1', NULL, 1, 'lhs', '{}', 1, '2026-01-01 00:00:00'),"
        " ('plan-c2', NULL, 2, 'ccd', '{}', 1, '2026-01-02 00:00:00'),"
        " ('plan-e1', 10, NULL, 'lhs', '{}', 2, '2026-01-03 00:00:00'),"
        " ('plan-orphan', NULL, NULL, 'lhs', '{}', NULL,"
        " '2026-01-04 00:00:00')",
    )
    _exec(
        db_url,
        "INSERT INTO wiki_pages (id, path, kind, source_ids, revision) VALUES"
        " ('w1', 'materials/x.md', 'material', '[\"src-a1\", \"src-a2\"]', 1),"
        " ('w2', 'materials/y.md', 'material', '[\"src-b1\"]', 1),"
        " ('w3', 'materials/z.md', 'material', '[\"src-a1\", \"src-b1\"]', 1),"
        " ('w4', 'materials/w.md', 'material', '[]', 1)",
    )


def _stamp(db_url: str, revision: str) -> None:
    from alembic import command

    command.stamp(make_config(db_url), revision)


def _project_map(db_url: str, table: str) -> dict[str, str | None]:
    return {r[0]: r[1] for r in _fetch_all(db_url, f"SELECT id, project_id FROM {table}")}


def test_0033_adds_columns_indexes_and_backfill(
    tmp_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create_pre_0033_schema(tmp_db_url)
    _seed_legacy_rows(tmp_db_url)
    _stamp(tmp_db_url, "0032")

    run_upgrade(tmp_db_url, monkeypatch)

    assert "project_id" in _column_names(tmp_db_url, "doe_plans")
    assert "project_id" in _column_names(tmp_db_url, "wiki_pages")
    assert "ix_doe_plans_project" in _index_names(tmp_db_url, "doe_plans")
    assert "ix_wiki_pages_project" in _index_names(tmp_db_url, "wiki_pages")

    plans = _project_map(tmp_db_url, "doe_plans")
    assert plans["plan-c1"] == "proj-A"  # via campaign
    assert plans["plan-c2"] == "proj-B"  # via campaign
    assert plans["plan-e1"] == "proj-A"  # via experiment
    assert plans["plan-orphan"] is None  # unattributable stays NULL

    pages = _project_map(tmp_db_url, "wiki_pages")
    assert pages["w1"] == "proj-A"  # all sources in proj-A
    assert pages["w2"] == "proj-B"
    assert pages["w3"] is None  # ambiguous across projects stays NULL
    assert pages["w4"] is None  # no sources stays NULL


def test_0033_idempotent_upgrade_twice(
    tmp_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create_pre_0033_schema(tmp_db_url)
    _seed_legacy_rows(tmp_db_url)
    _stamp(tmp_db_url, "0032")

    run_upgrade(tmp_db_url, monkeypatch)
    run_upgrade(tmp_db_url, monkeypatch)  # second run must be a silent no-op

    assert "project_id" in _column_names(tmp_db_url, "doe_plans")
    assert "project_id" in _column_names(tmp_db_url, "wiki_pages")
    plans = _project_map(tmp_db_url, "doe_plans")
    assert plans["plan-c1"] == "proj-A"


def test_0033_upgrade_head_on_empty_db(
    tmp_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full chain on an empty DB: 0033 must not raise and columns exist."""
    run_upgrade(tmp_db_url, monkeypatch)

    assert "project_id" in _column_names(tmp_db_url, "doe_plans")
    assert "project_id" in _column_names(tmp_db_url, "wiki_pages")
    assert "ix_doe_plans_project" in _index_names(tmp_db_url, "doe_plans")
    assert "ix_wiki_pages_project" in _index_names(tmp_db_url, "wiki_pages")


def test_0033_downgrade_drops_columns(
    tmp_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create_pre_0033_schema(tmp_db_url)
    _seed_legacy_rows(tmp_db_url)
    _stamp(tmp_db_url, "0032")
    run_upgrade(tmp_db_url, monkeypatch)
    assert "project_id" in _column_names(tmp_db_url, "doe_plans")

    run_downgrade(tmp_db_url, monkeypatch, "0032")

    assert "project_id" not in _column_names(tmp_db_url, "doe_plans")
    assert "project_id" not in _column_names(tmp_db_url, "wiki_pages")
    assert "ix_doe_plans_project" not in _index_names(tmp_db_url, "doe_plans")
    assert "ix_wiki_pages_project" not in _index_names(tmp_db_url, "wiki_pages")
    # Seed rows survive the downgrade (only the new columns are dropped).
    plans = _fetch_all(tmp_db_url, "SELECT id FROM doe_plans")
    assert {r[0] for r in plans} == {"plan-c1", "plan-c2", "plan-e1", "plan-orphan"}

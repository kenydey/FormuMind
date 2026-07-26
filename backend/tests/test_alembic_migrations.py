"""Tests for idempotent post-baseline Alembic migrations 0002-0006 (Task 0.2a).

Covers three guarantees:

1. A *legacy* database (pre-migration schema, missing the columns added by
   migrations 0002-0005 and still containing ``experiment_records``) gains
   every new column and loses the legacy table after ``alembic upgrade head``.
2. A *fresh* database (baseline 0001 already contains all columns) runs the
   same upgrade chain silently — every post-baseline migration is a no-op.
3. The revision chain is linear and ends at head ``0006``.

The Alembic-invocation helper mirrors ``tests/test_alembic_baseline.py``;
small duplication is intentional to keep the suites independent.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
SCRIPT_LOCATION = BACKEND_ROOT / "app" / "db" / "alembic"


def _make_config(db_url: str):
    """Build an Alembic Config bound to the repo's migration environment.

    Args:
        db_url: SQLAlchemy URL for the throwaway target database.

    Returns:
        A configured ``alembic.config.Config`` instance.
    """
    from alembic.config import Config

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(SCRIPT_LOCATION))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _column_names(db_url: str, table: str) -> set[str]:
    """Return the set of column names for ``table`` in the target database."""
    engine = create_engine(db_url)
    try:
        return {c["name"] for c in inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def _table_names(db_url: str) -> set[str]:
    """Return the set of table names present in the target database."""
    engine = create_engine(db_url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


@pytest.fixture()
def tmp_db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Point ``FORMUMIND_DB_URL`` at a fresh per-test SQLite database."""
    url = f"sqlite:///{tmp_path}/alembic_migrations_test.db"
    monkeypatch.setenv("FORMUMIND_DB_URL", url)
    return url


def _create_legacy_schema(db_url: str) -> None:
    """Create the pre-migration table shapes directly with raw SQL.

    Mirrors the schema as it existed before columns were added to the ORM
    models, plus the legacy ``experiment_records`` table that migration 0006
    is responsible for dropping.
    """
    engine = create_engine(db_url)
    statements = [
        """
        CREATE TABLE experiments (
            id INTEGER PRIMARY KEY,
            domain VARCHAR(64),
            factors JSON,
            cure_temperature_c FLOAT,
            measured JSON,
            source VARCHAR(64),
            label VARCHAR(255),
            created_at DATETIME
        )
        """,
        """
        CREATE TABLE campaigns (
            id INTEGER PRIMARY KEY,
            name VARCHAR(255),
            strategy VARCHAR(64),
            status VARCHAR(32),
            created_at DATETIME,
            updated_at DATETIME
        )
        """,
        """
        CREATE TABLE source_documents (
            id VARCHAR(36) PRIMARY KEY,
            filename VARCHAR(512),
            title VARCHAR(512),
            source_kind VARCHAR(32),
            content_hash VARCHAR(64),
            full_text TEXT,
            raw_text_chars INTEGER,
            source_guide JSON,
            extraction_status VARCHAR(32),
            extraction_error TEXT,
            created_at DATETIME
        )
        """,
        """
        CREATE TABLE document_chunks (
            id VARCHAR(36) PRIMARY KEY,
            source_id VARCHAR(36),
            ord INTEGER,
            text TEXT,
            heading_path VARCHAR(120),
            embedding JSON,
            embedding_model VARCHAR(80),
            created_at DATETIME
        )
        """,
        """
        CREATE TABLE kb_entity_links (
            id VARCHAR(36) PRIMARY KEY,
            src_entity_id VARCHAR(64),
            dst_entity_id VARCHAR(64),
            link_type VARCHAR(32),
            confidence FLOAT,
            evidence_refs JSON,
            created_at DATETIME
        )
        """,
        """
        CREATE TABLE experiment_records (
            id INTEGER PRIMARY KEY
        )
        """,
    ]
    try:
        with engine.begin() as conn:
            for stmt in statements:
                conn.execute(text(stmt))
    finally:
        engine.dispose()


def test_legacy_db_gets_columns_via_migration(tmp_db_url: str) -> None:
    """A legacy DB stamped at 0001 gains all new columns; legacy table is dropped."""
    from alembic import command

    _create_legacy_schema(tmp_db_url)

    cfg = _make_config(tmp_db_url)
    command.upgrade(cfg, "head")

    assert {"item_id", "project_id"} <= _column_names(tmp_db_url, "experiments")
    assert {
        "project_id",
        "primary_metric",
        "objectives_snapshot",
        "lever_snapshot",
        "sample_refs",
        "loop_history",
    } <= _column_names(tmp_db_url, "campaigns")
    assert {"origin_url", "project_id"} <= _column_names(tmp_db_url, "source_documents")
    assert {"page_no", "meta"} <= _column_names(tmp_db_url, "document_chunks")
    assert {
        "metadata_json",
        "is_valid",
        "extraction_method",
        "updated_at",
    } <= _column_names(tmp_db_url, "kb_entity_links")

    assert "experiment_records" not in _table_names(tmp_db_url)


def test_migrations_idempotent_on_fresh_db(tmp_db_url: str) -> None:
    """`upgrade head` on an empty DB runs 0002-0006 as silent no-ops."""
    from alembic import command

    cfg = _make_config(tmp_db_url)
    command.upgrade(cfg, "head")

    # Baseline 0001 already creates every column; the post-baseline
    # migrations must not raise and the full column set must be present.
    assert {"item_id", "project_id"} <= _column_names(tmp_db_url, "experiments")
    assert {
        "project_id",
        "primary_metric",
        "objectives_snapshot",
        "lever_snapshot",
        "sample_refs",
        "loop_history",
    } <= _column_names(tmp_db_url, "campaigns")
    assert {"origin_url", "project_id"} <= _column_names(tmp_db_url, "source_documents")
    assert {"page_no", "meta"} <= _column_names(tmp_db_url, "document_chunks")
    assert {
        "metadata_json",
        "is_valid",
        "extraction_method",
        "updated_at",
    } <= _column_names(tmp_db_url, "kb_entity_links")
    assert "experiment_records" not in _table_names(tmp_db_url)


def test_revision_chain_head_is_0006(tmp_db_url: str) -> None:
    """The revision chain is linear with a single head at revision ``0006``."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(ALEMBIC_INI))
    script = ScriptDirectory.from_config(cfg)

    heads = script.get_heads()
    assert len(heads) == 1, f"expected a single head, got {heads}"
    assert heads[0] == "0006"

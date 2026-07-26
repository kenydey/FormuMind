"""Tests for the Alembic baseline migration environment (Task 0.1).

Verifies that ``alembic upgrade head`` creates every ORM table on an empty
database and that ``alembic downgrade base`` drops them again, both driven
programmatically against a throwaway SQLite file pointed to by the
``FORMUMIND_DB_URL`` environment variable.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
SCRIPT_LOCATION = BACKEND_ROOT / "app" / "db" / "alembic"

EXPECTED_TABLES = {
    "experiments",
    "campaigns",
    "source_documents",
    "document_chunks",
    "kb_products",
    "kb_entities",
    "kb_mentions",
    "kb_entity_links",
    "projects",
}


def _make_config(db_url: str):
    """Build an Alembic Config bound to the repo's baseline environment.

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
    url = f"sqlite:///{tmp_path}/alembic_test.db"
    monkeypatch.setenv("FORMUMIND_DB_URL", url)
    return url


def test_upgrade_head_on_empty_db(tmp_db_url: str) -> None:
    """`upgrade head` on an empty DB creates all nine ORM tables."""
    from alembic import command

    cfg = _make_config(tmp_db_url)
    command.upgrade(cfg, "head")

    tables = _table_names(tmp_db_url)
    assert EXPECTED_TABLES <= tables, (
        f"missing tables after upgrade: {EXPECTED_TABLES - tables}"
    )


def test_downgrade_base_drops_tables(tmp_db_url: str) -> None:
    """`downgrade base` after `upgrade head` drops every migrated table."""
    from alembic import command

    cfg = _make_config(tmp_db_url)
    command.upgrade(cfg, "head")
    assert EXPECTED_TABLES <= _table_names(tmp_db_url)

    command.downgrade(cfg, "base")
    tables = _table_names(tmp_db_url)
    assert not (EXPECTED_TABLES & tables), (
        f"tables still present after downgrade: {EXPECTED_TABLES & tables}"
    )

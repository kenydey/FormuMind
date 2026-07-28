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
    "materials",
    "measurements",
    "experiment_attachments",
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


ENV_PY = BACKEND_ROOT / "app" / "db" / "alembic" / "env.py"


def _load_env_module(monkeypatch: pytest.MonkeyPatch):
    """Exec env.py with ``alembic.context`` stubbed so no migration runs.

    Returns:
        The loaded env module, exposing helpers like ``_resolve_url`` for
        direct unit testing.
    """
    import contextlib
    import importlib.util
    import types

    fake_context = types.SimpleNamespace(
        config=None,
        is_offline_mode=lambda: True,
        configure=lambda **_: None,
        begin_transaction=lambda: contextlib.nullcontext(),
        run_migrations=lambda: None,
    )
    monkeypatch.setattr("alembic.context", fake_context)
    spec = importlib.util.spec_from_file_location("formumind_alembic_env", ENV_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ini_script_location_resolves_from_any_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ini's script_location resolves even when cwd is not the backend root."""
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    monkeypatch.chdir(tmp_path)
    db_url = f"sqlite:///{tmp_path}/any_cwd.db"
    monkeypatch.setenv("FORMUMIND_DB_URL", db_url)

    # Deliberately no set_main_option("script_location", ...) override — the
    # ini alone must locate the migration scripts from any working directory.
    cfg = Config(str(ALEMBIC_INI))
    script = ScriptDirectory.from_config(cfg)
    assert Path(script.dir).resolve() == SCRIPT_LOCATION.resolve()

    command.upgrade(cfg, "head")
    assert EXPECTED_TABLES <= _table_names(db_url)


def test_resolve_url_failure_is_diagnosable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A settings failure with FORMUMIND_DB_URL unset surfaces a clear error."""
    monkeypatch.setenv("FORMUMIND_DB_URL", "sqlite:///:memory:")
    env = _load_env_module(monkeypatch)
    monkeypatch.delenv("FORMUMIND_DB_URL", raising=False)

    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr("app.config.get_settings", _boom)
    with pytest.raises(RuntimeError, match="FORMUMIND_DB_URL"):
        env._resolve_url()

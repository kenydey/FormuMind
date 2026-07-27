"""Shared Alembic invocation helpers for migration tests (Task 0.2b-2).

Any test that builds a *legacy* (pre-migration) schema by hand must run
``run_upgrade`` before touching ``make_engine`` / stores, because the runtime
schema guard in ``app.db.database`` now raises instead of ALTERing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
SCRIPT_LOCATION = BACKEND_ROOT / "app" / "db" / "alembic"


def make_config(db_url: str):
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


def run_upgrade(
    db_url: str, monkeypatch: pytest.MonkeyPatch, revision: str = "head"
) -> None:
    """Point ``FORMUMIND_DB_URL`` at ``db_url`` and run ``alembic upgrade``."""
    from alembic import command

    monkeypatch.setenv("FORMUMIND_DB_URL", db_url)
    command.upgrade(make_config(db_url), revision)


def run_downgrade(
    db_url: str, monkeypatch: pytest.MonkeyPatch, revision: str = "base"
) -> None:
    """Point ``FORMUMIND_DB_URL`` at ``db_url`` and run ``alembic downgrade``."""
    from alembic import command

    monkeypatch.setenv("FORMUMIND_DB_URL", db_url)
    command.downgrade(make_config(db_url), revision)

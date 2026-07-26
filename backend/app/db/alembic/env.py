"""Alembic migration environment for the FormuMind backend.

The target database URL is resolved from the ``FORMUMIND_DB_URL`` environment
variable first, falling back to ``app.config.get_settings().db_url`` so both
CI/test runs (env var) and local CLI usage (settings) work unchanged. Both
offline (SQL script generation) and online (direct connection) modes are
supported.
"""
from __future__ import annotations

import os
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db.models import Base

config = context.config

target_metadata = Base.metadata

# backend/ root — env.py lives at backend/app/db/alembic/env.py.
_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def _resolve_url() -> str:
    """Resolve the database URL for this migration run.

    Returns:
        The ``FORMUMIND_DB_URL`` environment variable when set, otherwise the
        URL configured in application settings. A cwd-relative SQLite URL
        (``sqlite:///./...``) is re-anchored at the backend root so running
        migrations from another working directory cannot silently create or
        modify a database file in the wrong location.

    Raises:
        RuntimeError: If ``FORMUMIND_DB_URL`` is unset and the application
            settings cannot be loaded.
    """
    env_url = os.environ.get("FORMUMIND_DB_URL")
    if env_url:
        url = env_url
    else:
        try:
            from app.config import get_settings

            url = get_settings().db_url
        except Exception as exc:
            raise RuntimeError(
                "Alembic: FORMUMIND_DB_URL is unset and app settings could "
                "not be loaded; set FORMUMIND_DB_URL or fix app configuration"
            ) from exc
    if url.startswith("sqlite:///./"):
        url = "sqlite:///" + (_BACKEND_ROOT / url[len("sqlite:///./"):]).as_posix()
    return url


def run_migrations_offline() -> None:
    """Run migrations in offline mode (emit SQL without a live connection)."""
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in online mode against a live database connection."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _resolve_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

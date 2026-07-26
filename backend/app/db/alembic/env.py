"""Alembic migration environment for the FormuMind backend.

The target database URL is resolved from the ``FORMUMIND_DB_URL`` environment
variable first, falling back to ``app.config.get_settings().db_url`` so both
CI/test runs (env var) and local CLI usage (settings) work unchanged. Both
offline (SQL script generation) and online (direct connection) modes are
supported.
"""
from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db.models import Base

config = context.config

target_metadata = Base.metadata


def _resolve_url() -> str:
    """Resolve the database URL for this migration run.

    Returns:
        The ``FORMUMIND_DB_URL`` environment variable when set, otherwise the
        URL configured in application settings.
    """
    env_url = os.environ.get("FORMUMIND_DB_URL")
    if env_url:
        return env_url
    from app.config import get_settings

    return get_settings().db_url


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

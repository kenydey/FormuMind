"""One-time migration: import a legacy ``experiments.json`` into the SQLite DB.

Called automatically on first startup after the B5 upgrade. Idempotent: the
JSON file is only read when the SQL database has *zero* rows, so re-running
never duplicates data. The original JSON file is not deleted — it serves as a
human-readable audit trail and a recovery source.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def migrate_from_stores(json_path: str, sql_store) -> int:
    """Copy records from a JSON store into a SQL store if the latter is empty.

    Accepts concrete store instances so callers (and tests) supply the
    back-ends directly without relying on global settings.
    Returns the number of records migrated (0 if nothing to do).
    """
    from .store import JsonExperimentStore

    if not Path(json_path).exists():
        return 0
    if sql_store.count() > 0:
        return 0  # DB already has data — nothing to migrate

    records = JsonExperimentStore(json_path).all()
    if not records:
        return 0

    sql_store.add(records)
    log.info("Migrated %d experiment record(s) from %s into SQLite.", len(records), json_path)
    return len(records)


def migrate_json_if_needed() -> int:
    """Migrate legacy experiments.json → SQLite using default settings/stores.

    Never raises — failures are logged and swallowed so a missing/corrupt JSON
    file never prevents the server from starting.
    """
    try:
        from ..config import get_settings
        from .database import default_session_factory
        from .store import SqlExperimentStore

        settings = get_settings()
        sql_store = SqlExperimentStore(default_session_factory())
        return migrate_from_stores(settings.experiments_path, sql_store)
    except Exception as exc:
        log.warning("Experiment migration skipped: %s", exc)
        return 0

"""One-time migration: import legacy experiment data into the active store.

Called automatically on first startup after store backend upgrades. Idempotent:
JSON is only read when the target store is empty; inline SQL rows migrate to
Datalab only when ``experiment_backend=datalab`` and the Datalab index is empty.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def migrate_from_stores(json_path: str, target_store) -> int:
    """Copy records from a JSON store into *target_store* if the latter is empty.

    Accepts concrete store instances so callers (and tests) supply the
    back-ends directly without relying on global settings.
    Returns the number of records migrated (0 if nothing to do).
    """
    from .store import JsonExperimentStore

    if not Path(json_path).exists():
        return 0
    if target_store.count() > 0:
        return 0

    records = JsonExperimentStore(json_path).all()
    if not records:
        return 0

    target_store.add(records)
    log.info("Migrated %d experiment record(s) from %s.", len(records), json_path)
    return len(records)


def migrate_inline_sql_to_datalab(legacy_store, datalab_store) -> int:
    """Move inline SQL experiment rows (item_id=NULL) into Datalab."""
    records = legacy_store.all()
    if not records:
        return 0
    if datalab_store.count() > 0:
        return 0
    legacy_store.clear()
    datalab_store.add(records)
    log.info("Migrated %d inline SQL experiment record(s) to Datalab.", len(records))
    return len(records)


def migrate_experiments_if_needed() -> int:
    """Migrate legacy JSON and inline SQL experiment data into the active store.

    Never raises — failures are logged and swallowed so a missing/corrupt JSON
    file never prevents the server from starting.
    """
    total = 0
    try:
        from ..config import get_settings
        from .database import default_session_factory
        from .store import SqlExperimentStore, get_experiment_store

        settings = get_settings()
        target = get_experiment_store()
        total += migrate_from_stores(settings.experiments_path, target)

        if settings.experiment_backend == "datalab":
            legacy = SqlExperimentStore(default_session_factory())
            total += migrate_inline_sql_to_datalab(legacy, target)
    except Exception as exc:
        log.warning("Experiment migration skipped: %s", exc)
    return total


def migrate_json_if_needed() -> int:
    """Backward-compatible alias for tests."""
    return migrate_experiments_if_needed()

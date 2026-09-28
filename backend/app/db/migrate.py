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
    datalab_store.add(records)
    legacy_store.clear()
    log.info("Migrated %d inline SQL experiment record(s) to Datalab.", len(records))
    return len(records)


def clean_doe_metadata_from_factors() -> int:
    """清洗历史脏数据：从 ExperimentRow.factors 移除 `_doe_metadata` 等非数值 blob。

    旧代码曾把嵌套 dict（`_doe_metadata`）塞进 factors 并持久化；新写入已只
    存数值（doe_cycle_service），但历史行仍带脏数据，下游按 dict[str, float]
    消费时会 TypeError。本函数幂等，只改确实含非数值值的行。
    返回清洗的行数。
    """
    from .database import default_session_factory
    from .models import ExperimentRow

    def _as_float(v: object) -> float | None:
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                return None
        return None

    cleaned = 0
    factory = default_session_factory()
    with factory() as session:
        rows = session.query(ExperimentRow).all()
        for row in rows:
            factors = row.factors or {}
            if not isinstance(factors, dict):
                continue
            scrubbed: dict[str, float] = {}
            for k, v in factors.items():
                f = _as_float(v)
                if f is not None:
                    scrubbed[str(k)] = f
            # 只有确实去掉东西时才写回（幂等，避免无谓的 UPDATE）
            if set(scrubbed) != set(factors):
                row.factors = scrubbed
                cleaned += 1
        session.commit()
    if cleaned:
        log.info("cleaned _doe_metadata/non-numeric factors from %d rows", cleaned)
    return cleaned


def migrate_experiments_if_needed() -> int:
    """Migrate legacy JSON and inline SQL experiment data into the active store.

    DatalabUnavailableError is degraded (logged) so a missing Datalab service
    never prevents the server from starting; other unexpected exceptions
    propagate so real bugs are not silently swallowed.
    """
    from .datalab_client import DatalabUnavailableError

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

        # 历史脏数据：清洗 factors 里残留的 _doe_metadata 等非数值 blob（幂等）
        try:
            clean_doe_metadata_from_factors()
        except Exception as exc:  # noqa: BLE001
            log.warning("doe_metadata cleanup skipped: %s", exc)
    except DatalabUnavailableError as exc:
        log.warning("Experiment migration skipped (Datalab unavailable): %s", exc)
    return total

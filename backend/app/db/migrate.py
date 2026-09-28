"""One-time migration: import legacy experiment data into the active store.

Called automatically on first startup after store backend upgrades. Idempotent:
JSON is only read when the target store is empty; inline SQL rows migrate to
Datalab only when ``experiment_backend=datalab`` and the Datalab index is empty.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# 一次性数据迁移标记（与 alembic schema 版本机制分离）：
# - alembic 管 schema（列/表结构），且不会在启动时自动执行；
# - fm_data_migrations 记录数据清洗类一次性任务，由 migrate.py 自查自写，
#   标记检查 → 清洗 → 写标记在同一事务内提交，避免"洗一半崩了下次跳过"。
_DATA_MIGRATION_TABLE = "fm_data_migrations"
_DOE_METADATA_CLEANUP = "doe_metadata_cleanup_v1"


def _data_migration_table():
    """fm_data_migrations 表定义（name 主键防并发双写）。"""
    from sqlalchemy import Column, MetaData, String, Table

    return Table(
        _DATA_MIGRATION_TABLE,
        MetaData(),
        Column("name", String(128), primary_key=True),
        Column("done_at", String(32), nullable=False),
    )


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
    消费时会 TypeError。

    一次性执行：首次运行时全表扫描清洗，并在同一事务内写入
    fm_data_migrations 标记；之后每次启动直接跳过，不再扫描 experiments 表。
    幂等：清洗本身只改确实含非数值值的行（键集合变化才写回），标记丢失重跑
    也不会破坏数据；并发双跑时 name 主键保证标记唯一，后提交者回滚重试即
    可（清洗幂等，重跑无害）。
    返回清洗的行数（已标记跳过时返回 0）。
    """
    from datetime import datetime, timezone

    from sqlalchemy.exc import IntegrityError, OperationalError
    from sqlalchemy import select

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

    marker_table = _data_migration_table()
    factory = default_session_factory()
    with factory() as session:
        # 标记表不存在即建（幂等；并发建表竞态时忽略已存在错误）
        try:
            marker_table.create(session.get_bind(), checkfirst=True)
        except OperationalError as exc:
            if "already exists" not in str(exc).lower():
                raise
            session.rollback()

        already = session.execute(
            select(marker_table.c.name).where(
                marker_table.c.name == _DOE_METADATA_CLEANUP
            )
        ).first()
        if already is not None:
            return 0

        cleaned = 0
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

        # 清洗与标记写入同一事务提交：中途崩溃整体回滚，下次重跑而非跳过。
        # 并发双跑时后提交者撞 name 主键 → 回滚（对方已做完等价幂等清洗）。
        try:
            session.execute(
                marker_table.insert().values(
                    name=_DOE_METADATA_CLEANUP,
                    done_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                )
            )
            session.commit()
        except IntegrityError:
            session.rollback()
            log.info("doe_metadata cleanup marker already written by peer; skipped")
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

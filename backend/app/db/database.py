"""Engine + session factory.

SQLite by default (zero-config, file-backed, ships with Python). ``check_same_thread``
is disabled and a generous busy ``timeout`` is set so the thread-backed
TaskManager / in-process workers can share one connection safely; point
``FORMUMIND_DB_URL`` at Postgres for true multi-process concurrency.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def _ensure_sqlite_dir(db_url: str) -> None:
    prefix = "sqlite:///"
    if db_url.startswith(prefix):
        path = db_url[len(prefix):]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)


def _configure_sqlite(engine: Engine) -> None:
    """Per-connection SQLite pragmas: foreign keys, and WAL journalling.

    **foreign_keys.** SQLite ships with ``PRAGMA foreign_keys=OFF`` and applies
    it per connection, so a declared ForeignKey is decorative until this runs —
    dev and the whole test suite would happily accept orphan rows that
    PostgreSQL rejects in production, which is the worst way to find out.

    **journal_mode=WAL.** The default is ``delete`` (a rollback journal), under
    which readers and writers are mutually exclusive: any write takes an
    exclusive lock on the whole database and every reader blocks behind it.
    This process is thread-backed by design — the TaskManager, the in-process
    workers and the request threads all share one file — so a long ingest
    transaction stalls unrelated reads until it commits, which is what "database
    is locked" in production looks like. WAL lets any number of readers run
    concurrently with one writer.

    Set per connection rather than once, because the mode lives in the database
    file and a caller may point at a file some other process created in
    ``delete`` mode. Failure is not fatal: WAL is unavailable for ``:memory:``
    databases and on network filesystems, and neither is a reason to refuse to
    start — those deployments simply keep the old locking behaviour.
    """
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, _record):  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            # Wait up to 60 s before raising "database is locked" — Celery
            # and Uvicorn share the same file; a write arriving during another
            # write should block instead of failing immediately. A flush-level
            # retry is NOT viable: SQLite's "database is locked" invalidates the
            # SQLAlchemy connection, so re-flushing raises "transaction is
            # inactive". 60s is a middle ground: long enough to ride out a
            # concurrent 261 KB payload write, short enough that a genuinely
            # stuck lock fails a test quickly instead of hanging it.
            cursor.execute("PRAGMA busy_timeout=60000")
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
            except Exception:
                # An in-memory or network-hosted database cannot do WAL. Losing
                # the concurrency win is acceptable; failing to connect is not.
                pass
        finally:
            cursor.close()


def make_engine(db_url: str) -> Engine:
    _ensure_sqlite_dir(db_url)
    connect_args: dict = {}
    if db_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(db_url, future=True, connect_args=connect_args)
    if db_url.startswith("sqlite"):
        _configure_sqlite(engine)
    from ..config import get_settings

    if get_settings().environment not in ("production", "prod"):
        Base.metadata.create_all(engine)
    _ensure_experiment_columns(engine)
    _ensure_campaign_columns(engine)
    _ensure_source_document_columns(engine)
    _ensure_kb_entity_link_columns(engine)
    _ensure_material_columns(engine)
    return engine


def _require_columns(engine: Engine, table: str, columns: tuple[str, ...]) -> None:
    """只读守护：表存在但缺列时给出可操作错误（不再运行时 ALTER）。

    一次性报出全部缺失列，避免用户多轮运行才发现完整 drift。
    """
    from sqlalchemy import inspect

    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns(table)}
    missing = [f"{table}.{col}" for col in columns if col not in existing]
    if missing:
        raise RuntimeError(
            f"schema drift: 缺失列 {', '.join(missing)}，请先运行: alembic upgrade head"
        )


def _ensure_experiment_columns(engine: Engine) -> None:
    """只读守护：experiments 表必须具备 Phase 2 索引列。"""
    _require_columns(engine, "experiments", ("item_id", "project_id"))


def _ensure_campaign_columns(engine: Engine) -> None:
    """只读守护：campaigns 表必须具备 workbench / Datalab 列。"""
    _require_columns(
        engine,
        "campaigns",
        (
            "project_id",
            "primary_metric",
            "objectives_snapshot",
            "lever_snapshot",
            "sample_refs",
            "loop_history",
        ),
    )


def _ensure_source_document_columns(engine: Engine) -> None:
    """只读守护：source_documents 表必须具备 async-ingest 溯源列。"""
    _require_columns(engine, "source_documents", ("origin_url", "project_id"))
    _ensure_document_chunk_columns(engine)


def _ensure_document_chunk_columns(engine: Engine) -> None:
    """只读守护：document_chunks 表必须具备溯源 / 实体元数据列。"""
    _require_columns(engine, "document_chunks", ("page_no", "meta"))


def _ensure_kb_entity_link_columns(engine: Engine) -> None:
    """只读守护：kb_entity_links 表必须具备语义关系列。"""
    _require_columns(
        engine,
        "kb_entity_links",
        ("metadata_json", "is_valid", "extraction_method", "updated_at"),
    )


def _ensure_material_columns(engine: Engine) -> None:
    """只读守护：materials 表必须具备替代/采购元数据列。"""
    _require_columns(
        engine,
        "materials",
        ("norm_key", "name", "origin", "availability", "functional_class"),
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


# Lazily-built default engine/session factory (built on first use so importing
# this module never touches the filesystem at import time).
_default: dict[str, object] = {}
_default_lock = threading.Lock()


def default_session_factory() -> sessionmaker[Session]:
    from ..config import get_settings

    db_url = os.environ.get("FORMUMIND_DB_URL") or get_settings().db_url
    with _default_lock:
        if _default.get("url") != db_url:
            if _default.get("engine"):
                try:
                    _default["engine"].dispose()  # type: ignore[union-attr]
                except Exception:
                    pass
            engine = make_engine(db_url)
            _default["url"] = db_url
            _default["engine"] = engine
            _default["factory"] = make_session_factory(engine)
    return _default["factory"]  # type: ignore[return-value]

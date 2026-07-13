"""Engine + session factory.

SQLite by default (zero-config, file-backed, ships with Python). ``check_same_thread``
is disabled and a generous busy ``timeout`` is set so the thread-backed
TaskManager / in-process workers can share one connection safely; point
``FORMUMIND_DB_URL`` at Postgres for true multi-process concurrency.
"""
from __future__ import annotations

import os
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


def make_engine(db_url: str) -> Engine:
    _ensure_sqlite_dir(db_url)
    connect_args: dict = {}
    if db_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(db_url, future=True, connect_args=connect_args)
    Base.metadata.create_all(engine)
    _drop_legacy_workbench_table(engine)
    _ensure_experiment_columns(engine)
    _ensure_campaign_columns(engine)
    _ensure_source_document_columns(engine)
    return engine


def _ensure_experiment_columns(engine: Engine) -> None:
    """Add Phase 2 index columns to legacy experiments tables."""
    from sqlalchemy import inspect, text

    if "experiments" not in inspect(engine).get_table_names():
        return
    cols = {c["name"] for c in inspect(engine).get_columns("experiments")}
    with engine.begin() as conn:
        if "item_id" not in cols:
            conn.execute(text("ALTER TABLE experiments ADD COLUMN item_id VARCHAR(128)"))
        if "project_id" not in cols:
            conn.execute(text("ALTER TABLE experiments ADD COLUMN project_id VARCHAR(36) DEFAULT ''"))


def _ensure_campaign_columns(engine: Engine) -> None:
    """Add workbench / Datalab columns to legacy campaigns tables."""
    from sqlalchemy import inspect, text

    if "campaigns" not in inspect(engine).get_table_names():
        return
    cols = {c["name"] for c in inspect(engine).get_columns("campaigns")}
    with engine.begin() as conn:
        if "project_id" not in cols:
            conn.execute(text("ALTER TABLE campaigns ADD COLUMN project_id VARCHAR(36)"))
        if "primary_metric" not in cols:
            conn.execute(text("ALTER TABLE campaigns ADD COLUMN primary_metric VARCHAR(64)"))
        if "objectives_snapshot" not in cols:
            conn.execute(text("ALTER TABLE campaigns ADD COLUMN objectives_snapshot JSON"))
        if "lever_snapshot" not in cols:
            conn.execute(text("ALTER TABLE campaigns ADD COLUMN lever_snapshot JSON"))
        if "sample_refs" not in cols:
            conn.execute(text("ALTER TABLE campaigns ADD COLUMN sample_refs JSON DEFAULT '[]'"))


def _ensure_source_document_columns(engine: Engine) -> None:
    """Add async-ingest provenance columns to legacy source_documents tables."""
    from sqlalchemy import inspect, text

    if "source_documents" not in inspect(engine).get_table_names():
        return
    cols = {c["name"] for c in inspect(engine).get_columns("source_documents")}
    with engine.begin() as conn:
        if "origin_url" not in cols:
            conn.execute(text("ALTER TABLE source_documents ADD COLUMN origin_url VARCHAR(1024)"))


def _drop_legacy_workbench_table(engine: Engine) -> None:
    """Remove deprecated experiment_records table (workbench SSOT → Datalab)."""
    from sqlalchemy import inspect, text

    if "experiment_records" not in inspect(engine).get_table_names():
        return
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS experiment_records"))


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


# Lazily-built default engine/session factory (built on first use so importing
# this module never touches the filesystem at import time).
_default: dict[str, object] = {}


def default_session_factory() -> sessionmaker[Session]:
    from ..config import get_settings

    db_url = os.environ.get("FORMUMIND_DB_URL") or get_settings().db_url
    if _default.get("url") != db_url:
        engine = make_engine(db_url)
        _default["url"] = db_url
        _default["factory"] = make_session_factory(engine)
    return _default["factory"]  # type: ignore[return-value]

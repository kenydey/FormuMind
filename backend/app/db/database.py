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
    return engine


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

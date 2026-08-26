"""SQLAlchemy write-session helpers with explicit rollback on failure."""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)


def _commit_with_retry(session: Session, *, max_attempts: int = 6) -> None:
    """Commit, retrying on SQLite "database is locked" with backoff.

    WAL + ``busy_timeout`` already let one writer proceed while another waits,
    but the wait is bounded and two Celery prefork workers ingesting in
    parallel can still exhaust it. A commit that loses the race is retried a
    few times with exponential backoff instead of failing the whole document —
    this is the "database is locked" that used to fail dozens of fetched
    documents per batch.

    The retry covers both failure phases: a lock error raised during the
    implicit flush (INSERT/UPDATE) leaves the session in pending-rollback, so
    we ``rollback()`` before retrying — otherwise the next ``commit()`` would
    raise ``PendingRollbackError`` and defeat the retry entirely.
    """
    delay = 0.5
    for attempt in range(max_attempts):
        try:
            session.commit()
            return
        except OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            if attempt == max_attempts - 1:
                raise
            try:
                session.rollback()
            except Exception:
                logger.exception("rollback during lock-retry also failed")
                raise
            time.sleep(delay)
            delay *= 2


@contextmanager
def commit_session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Yield a session, commit (with lock retry) on success, rollback on failure.

    The whole write runs under a cross-process Redis lock so uvicorn and celery
    take turns writing SQLite instead of racing to "database is locked".
    """
    from .sqlite_lock import sqlite_write_lock
    from ..config import get_settings

    with sqlite_write_lock(get_settings().redis_url):
        with session_factory() as session:
            try:
                yield session
                _commit_with_retry(session)
            except Exception:
                try:
                    session.rollback()
                except Exception:
                    logger.exception("rollback also failed")
                raise

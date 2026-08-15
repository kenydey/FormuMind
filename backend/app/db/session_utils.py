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
            time.sleep(delay)
            delay *= 2


@contextmanager
def commit_session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Yield a session, commit (with lock retry) on success, rollback on failure."""
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

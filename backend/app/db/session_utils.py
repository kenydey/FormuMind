"""SQLAlchemy write-session helpers with explicit rollback on failure."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session, sessionmaker


@contextmanager
def commit_session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Yield a session, commit on success, rollback on any exception."""
    with session_factory() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

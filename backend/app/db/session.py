"""Session utilities for direct database access."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy.orm import Session

from .database import default_session_factory


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """Yield a database session with automatic commit/rollback."""
    factory = default_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

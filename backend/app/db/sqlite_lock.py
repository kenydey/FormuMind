"""Cross-process SQLite write lock backed by Redis.

SQLite serializes writes at the database level anyway, but two processes
(uvicorn autosave + celery kb_ingest) can both try to write at once and one
loses the busy_timeout race with "database is locked". A Redis lock held across
the write transaction makes the two processes take turns instead of colliding.

Degrades to no-op (proceed unlocked) when Redis is unavailable, so a lost Redis
never takes down writes — it just falls back to busy_timeout + retry.
"""
from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator

logger = logging.getLogger(__name__)

_LOCK_KEY = "formumind:sqlite_write"


@contextlib.contextmanager
def sqlite_write_lock(
    redis_url: str | None, *, timeout: float = 300.0, blocking_timeout: float = 300.0
) -> Iterator[None]:
    """Hold a cross-process lock for the duration of a SQLite write transaction."""
    if not redis_url:
        yield
        return

    # Acquire outside the yield so an exception raised by the caller's body is
    # never swallowed by the Redis error handler.
    lock = None
    try:
        import redis

        client = redis.from_url(redis_url, decode_responses=True, socket_timeout=5)
        lock = client.lock(_LOCK_KEY, timeout=timeout, blocking_timeout=blocking_timeout)
        acquired = lock.acquire(blocking=True, blocking_timeout=blocking_timeout)
    except Exception as exc:
        logger.warning("Redis write lock unavailable (%s) — proceeding unlocked", exc)
        yield
        return

    if not acquired:
        logger.warning(
            "SQLite write lock timeout after %.0fs — proceeding unlocked", blocking_timeout
        )
        yield
        return

    try:
        yield
    finally:
        try:
            lock.release()
        except Exception:
            pass

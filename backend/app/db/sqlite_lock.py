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
    redis_url: str | None, *, timeout: float = 30.0, blocking_timeout: float = 30.0
) -> Iterator[None]:
    """Hold a cross-process lock for the duration of a SQLite write transaction.

    Reduced default timeout/blocking to 30s to fail fast and surface contention
    rather than stalling callers for five minutes.
    """
    from ..services import redis_breaker

    if not redis_url or redis_breaker.is_open():
        yield
        return

    # Acquire outside the yield so an exception raised by the caller's body is
    # never swallowed by the Redis error handler.
    lock = None
    client = None
    try:
        import redis

        client = redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=redis_breaker.SOCKET_TIMEOUT_S,
            socket_connect_timeout=redis_breaker.CONNECT_TIMEOUT_S,
        )
        lock = client.lock(_LOCK_KEY, timeout=timeout, blocking_timeout=blocking_timeout)
        acquired = lock.acquire(blocking=True, blocking_timeout=blocking_timeout)
    except Exception as exc:
        redis_breaker.trip()
        logger.warning(
            "Redis write lock unavailable (%s) — proceeding unlocked, not retrying for %.0f s",
            exc,
            redis_breaker.RETRY_AFTER_S,
        )
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

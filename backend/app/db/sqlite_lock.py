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
import time
from collections.abc import Iterator

logger = logging.getLogger(__name__)

_LOCK_KEY = "formumind:sqlite_write"

# Redis down is the normal state of a development install (and of every test run). Every write goes through here,
# so without a back-off each one paid a connection attempt and logged a WARNING: instant on Linux, ~2 s per write
# on Windows (a refused loopback connection is retried by the TCP stack there) — a dev install without Redis crawled.
# After a failure the lock is skipped for this long; a Redis that comes up is picked up at the next attempt.
_RETRY_AFTER_S = 30.0
_CONNECT_TIMEOUT_S = 1.0
_monotonic = time.monotonic
_redis_down_until = 0.0


@contextlib.contextmanager
def sqlite_write_lock(
    redis_url: str | None, *, timeout: float = 30.0, blocking_timeout: float = 30.0
) -> Iterator[None]:
    """Hold a cross-process lock for the duration of a SQLite write transaction.

    Reduced default timeout/blocking to 30s to fail fast and surface contention
    rather than stalling callers for five minutes.
    """
    global _redis_down_until
    if not redis_url or _monotonic() < _redis_down_until:
        yield
        return

    # Acquire outside the yield so an exception raised by the caller's body is
    # never swallowed by the Redis error handler.
    lock = None
    client = None
    try:
        import redis

        client = redis.from_url(
            redis_url, decode_responses=True, socket_timeout=5, socket_connect_timeout=_CONNECT_TIMEOUT_S
        )
        lock = client.lock(_LOCK_KEY, timeout=timeout, blocking_timeout=blocking_timeout)
        acquired = lock.acquire(blocking=True, blocking_timeout=blocking_timeout)
    except Exception as exc:
        _redis_down_until = _monotonic() + _RETRY_AFTER_S
        logger.warning(
            "Redis write lock unavailable (%s) — proceeding unlocked, not retrying for %.0f s", exc, _RETRY_AFTER_S
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

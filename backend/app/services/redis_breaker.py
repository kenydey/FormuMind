"""A back-off for a Redis that is not there.

Redis being absent is the normal state of a development install (and of every test run), yet most of the code that
talks to it opens a fresh connection per call and treats a failure as "fall back to disk / proceed unlocked". Each
such call used to pay for a connection attempt and log a WARNING:

* on Linux a refused loopback connection fails at once, so it only cost log noise;
* on Windows the TCP stack retries a refused connection (~2 s), so every progress event of every background task
  and every database write cost two seconds — an ingest that takes a second on Linux did not finish in thirty, and
  the whole backend test suite took more than an hour.

After a connection-level failure the breaker stays open for ``RETRY_AFTER_S``; callers skip Redis while it is open
(they all already have the fallback). A Redis that comes up is picked up at the next attempt after the window.
"""
from __future__ import annotations

import time

RETRY_AFTER_S = 30.0
CONNECT_TIMEOUT_S = 1.0
SOCKET_TIMEOUT_S = 5.0

_monotonic = time.monotonic
_open_until = 0.0


def is_open() -> bool:
    """True while attempts should be skipped."""
    return _monotonic() < _open_until


def trip() -> None:
    """Record a connection-level failure: skip Redis for the next ``RETRY_AFTER_S`` seconds."""
    global _open_until
    _open_until = _monotonic() + RETRY_AFTER_S


def reset() -> None:
    global _open_until
    _open_until = 0.0


def was_refused(exc: BaseException) -> bool:
    """True when *exc* is the breaker's own refusal, not a failure of a real connection attempt."""
    return bool(getattr(exc, "breaker_refusal", False))


def note_failure(exc: BaseException) -> bool:
    """Open the breaker if *exc* says Redis could not be reached; returns whether it did.

    For callers that cannot use :func:`client_from_url` — the ``redis.asyncio`` client — but want the same
    "don't retry for a while" behaviour. Errors the server *answered* (a bad command, a full disk) do not count,
    and neither does the breaker's own refusal: re-arming the window on every refused call would keep it shut for
    as long as traffic continues, and a Redis that came up in the meantime would never be noticed.
    """
    import redis

    if was_refused(exc):
        return False
    if isinstance(exc, (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError)):
        trip()
        return True
    return False


def refuse_if_open() -> None:
    """Raise ``ConnectionError`` at once while the breaker is open (the same error a refused connection gives)."""
    if is_open():
        import redis

        exc = redis.exceptions.ConnectionError("Redis was unreachable a moment ago; not retrying yet")
        exc.breaker_refusal = True  # type: ignore[attr-defined]
        raise exc


class GuardedRedis:
    """A Redis client that trips the breaker when a call fails to reach the server.

    Every public method of the wrapped client is passed through; ``ConnectionError`` / ``TimeoutError`` from a call
    open the breaker and propagate unchanged, so the caller's existing fallback still runs.
    """

    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if not callable(attr):
            return attr
        import redis

        def call(*args, **kwargs):
            try:
                return attr(*args, **kwargs)
            except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError):
                trip()
                raise

        return call


def client_from_url(url: str, **kwargs):
    """A breaker-guarded ``redis.Redis``; raises ``ConnectionError`` at once while the breaker is open."""
    import redis

    refuse_if_open()
    kwargs.setdefault("socket_connect_timeout", CONNECT_TIMEOUT_S)
    kwargs.setdefault("socket_timeout", SOCKET_TIMEOUT_S)
    return GuardedRedis(redis.Redis.from_url(url, **kwargs))

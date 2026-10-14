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


def _class_names(exc: BaseException) -> set[str]:
    # By name, not isinstance: redis may be absent or stubbed, and the builtin ConnectionError/TimeoutError count too.
    return {cls.__name__ for cls in type(exc).__mro__}


def is_slow_reply(exc: BaseException) -> bool:
    """True for a timeout on a *live* connection (``Timeout reading from socket``): the server is there, just slow."""
    if "TimeoutError" not in _class_names(exc):
        return False
    text = str(exc).lower()
    return any(word in text for word in ("reading", "writing", "read from", "send"))


def is_unreachable(exc: BaseException) -> bool:
    """True when *exc* says the server could not be reached, as opposed to it answering slowly.

    ``ConnectionError`` always means unreachable. A ``TimeoutError`` while *connecting* does too, but a slow reply must
    not: one slow ``GET`` of a large value would otherwise switch Redis off, and with it the cross-process SQLite lock,
    for every caller in the process.
    """
    names = _class_names(exc)
    if "ConnectionError" in names:
        return True
    return "TimeoutError" in names and not is_slow_reply(exc)


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
    if was_refused(exc):
        return False
    if is_unreachable(exc):
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


# Client methods that hand back an object which talks to the server itself (``pipeline().execute()``,
# ``pubsub().get_message()``, ``lock().acquire()``): those objects are guarded too.
_RETURNS_CONNECTED_OBJECT = frozenset({"pipeline", "pubsub", "lock"})


class GuardedRedis:
    """A Redis client that trips the breaker when a call fails to reach the server.

    Every public method of the wrapped client is passed through; an unreachable-server error from a call (see
    :func:`is_unreachable`) opens the breaker and propagates unchanged, so the caller's existing fallback still runs.
    """

    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if not callable(attr):
            return attr
        wrap_result = name in _RETURNS_CONNECTED_OBJECT

        def call(*args, **kwargs):
            try:
                result = attr(*args, **kwargs)
            except Exception as exc:
                if is_unreachable(exc):
                    trip()
                raise
            return GuardedRedis(result) if wrap_result else result

        return call

    def __enter__(self):  # ``with client.pipeline() as pipe`` / ``with client.lock(...)``
        entered = self._client.__enter__()
        return GuardedRedis(entered) if entered is not self._client else self

    def __exit__(self, *exc_info):
        return self._client.__exit__(*exc_info)


def client_from_url(url: str, **kwargs):
    """A breaker-guarded ``redis.Redis``; raises ``ConnectionError`` at once while the breaker is open."""
    import redis

    from ..redis_compat import client_kwargs

    refuse_if_open()
    kwargs.setdefault("socket_connect_timeout", CONNECT_TIMEOUT_S)
    kwargs.setdefault("socket_timeout", SOCKET_TIMEOUT_S)
    for name, value in client_kwargs().items():  # the protocol, said explicitly: see app/redis_compat.py
        kwargs.setdefault(name, value)
    return GuardedRedis(redis.Redis.from_url(url, **kwargs))

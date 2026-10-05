"""A Redis that is not there must cost one failed connection per half minute, not one per write (round-4).

``commit_session`` — every database write — takes a cross-process Redis lock and, when Redis is unreachable,
proceeds unlocked. Redis being absent is the normal state of a development install, so that fallback ran, and
logged a WARNING, on every single write. On Linux a refused loopback connection fails instantly; on Windows the TCP
stack retries it (~2 s), so a development install without Redis spent two seconds per write.
"""
from __future__ import annotations

import logging
import sys
import types

import pytest

from app.db import sqlite_lock
from app.services import redis_breaker


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture()
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(redis_breaker, "_monotonic", c)
    monkeypatch.setattr(redis_breaker, "_open_until", 0.0)
    return c


def _fake_redis(monkeypatch, *, down: bool, acquired: bool = True):
    calls = {"from_url": 0, "acquire": 0, "release": 0, "kwargs": None}

    class _Lock:
        def acquire(self, **_kw):
            calls["acquire"] += 1
            return acquired

        def release(self):
            calls["release"] += 1

    class _Client:
        def lock(self, *_a, **_kw):
            return _Lock()

    def from_url(url, **kwargs):
        calls["from_url"] += 1
        calls["kwargs"] = kwargs
        if down:
            raise ConnectionError("Error 111 connecting to localhost:6379. Connection refused.")
        return _Client()

    monkeypatch.setitem(sys.modules, "redis", types.SimpleNamespace(from_url=from_url))
    return calls


def test_a_missing_redis_is_tried_once_then_left_alone(clock, monkeypatch, caplog):
    calls = _fake_redis(monkeypatch, down=True)
    with caplog.at_level(logging.WARNING, logger=sqlite_lock.logger.name):
        for _ in range(8):
            with sqlite_lock.sqlite_write_lock("redis://localhost:6379/0"):
                pass
    assert calls["from_url"] == 1
    warnings = [r for r in caplog.records if "Redis write lock unavailable" in r.getMessage()]
    assert len(warnings) == 1, "one WARNING per back-off window, not one per write"


def test_it_is_tried_again_after_the_window(clock, monkeypatch):
    calls = _fake_redis(monkeypatch, down=True)
    with sqlite_lock.sqlite_write_lock("redis://x"):
        pass
    clock.now += redis_breaker.RETRY_AFTER_S - 1
    with sqlite_lock.sqlite_write_lock("redis://x"):
        pass
    assert calls["from_url"] == 1
    clock.now += 2
    with sqlite_lock.sqlite_write_lock("redis://x"):
        pass
    assert calls["from_url"] == 2


def test_a_redis_that_comes_up_is_used_again(clock, monkeypatch):
    _fake_redis(monkeypatch, down=True)
    with sqlite_lock.sqlite_write_lock("redis://x"):
        pass
    clock.now += redis_breaker.RETRY_AFTER_S + 1
    calls = _fake_redis(monkeypatch, down=False)
    with sqlite_lock.sqlite_write_lock("redis://x"):
        pass
    assert (calls["acquire"], calls["release"]) == (1, 1)


def test_a_healthy_redis_is_locked_on_every_call(clock, monkeypatch):
    calls = _fake_redis(monkeypatch, down=False)
    for _ in range(3):
        with sqlite_lock.sqlite_write_lock("redis://x"):
            pass
    assert (calls["from_url"], calls["acquire"], calls["release"]) == (3, 3, 3)


def test_the_connection_attempt_is_bounded(clock, monkeypatch):
    calls = _fake_redis(monkeypatch, down=False)
    with sqlite_lock.sqlite_write_lock("redis://x"):
        pass
    assert calls["kwargs"]["socket_connect_timeout"] == redis_breaker.CONNECT_TIMEOUT_S


def test_without_a_url_redis_is_never_touched(clock, monkeypatch):
    calls = _fake_redis(monkeypatch, down=True)
    with sqlite_lock.sqlite_write_lock(None):
        pass
    assert calls["from_url"] == 0


def test_an_error_in_the_body_is_not_swallowed_by_the_handler(clock, monkeypatch):
    _fake_redis(monkeypatch, down=True)
    with pytest.raises(RuntimeError, match="boom"):
        with sqlite_lock.sqlite_write_lock("redis://x"):
            raise RuntimeError("boom")

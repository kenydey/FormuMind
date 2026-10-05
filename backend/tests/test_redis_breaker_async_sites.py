"""The asyncio Redis users honour the breaker too (round-4).

The chat-session hot cache and the SSE stream each opened their own connection per call. With no Redis that is
free on Linux and ~2 s per attempt on Windows — every saved chat message, every opened progress stream. They now
skip Redis while the breaker is open, trip it on a connection-level failure, and the breaker's own refusal does not
re-arm the window (or a Redis that came back would never be noticed under steady traffic).
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid

import pytest
import redis
import redis.asyncio as rredis
from fastapi.testclient import TestClient

from app.services import redis_breaker


class _Clock:
    now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture()
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(redis_breaker, "_monotonic", c)
    monkeypatch.setattr(redis_breaker, "_open_until", 0.0)
    return c


def test_only_connection_level_errors_open_the_breaker(clock):
    assert redis_breaker.note_failure(redis.exceptions.ResponseError("WRONGTYPE")) is False
    assert not redis_breaker.is_open()
    assert redis_breaker.note_failure(redis.exceptions.TimeoutError("timed out")) is True
    assert redis_breaker.is_open()


def test_the_breakers_own_refusal_does_not_re_arm_the_window(clock):
    redis_breaker.trip()
    window_end = redis_breaker._open_until
    clock.now += 10
    with pytest.raises(redis.exceptions.ConnectionError) as refused:
        redis_breaker.refuse_if_open()
    assert redis_breaker.was_refused(refused.value)
    assert redis_breaker.note_failure(refused.value) is False
    assert redis_breaker._open_until == window_end
    clock.now = window_end + 1  # the window ends on schedule, however many calls were refused meanwhile
    redis_breaker.refuse_if_open()  # no longer raises


def test_a_real_connection_error_is_not_mistaken_for_a_refusal(clock):
    error = redis.exceptions.ConnectionError("Error 10061 connecting to localhost:6379")
    assert not redis_breaker.was_refused(error)


class _DownClient:
    def __init__(self):
        self.calls = 0

    async def setex(self, *args, **kwargs):
        self.calls += 1
        raise redis.exceptions.ConnectionError("Error 10061 connecting to localhost:6379")

    async def delete(self, *args, **kwargs):
        self.calls += 1
        raise redis.exceptions.ConnectionError("Error 10061 connecting to localhost:6379")


def test_the_chat_hot_cache_stops_reconnecting_once_redis_is_known_to_be_down(clock, monkeypatch):
    from app.services.session.memory_service import SessionMemoryService

    client = _DownClient()
    built: list[dict] = []
    monkeypatch.setattr(rredis, "from_url", lambda url, **kw: built.append(kw) or client)
    service = SessionMemoryService()

    asyncio.run(service._cache_write("s1", [], {}))
    assert client.calls == 1 and redis_breaker.is_open()
    assert built[0]["socket_connect_timeout"] == redis_breaker.CONNECT_TIMEOUT_S

    for _ in range(5):
        asyncio.run(service._cache_write("s1", [], {}))
        asyncio.run(service._cache_drop("s1"))
    assert client.calls == 1, "a connection attempt was made while the breaker was open"

    clock.now += redis_breaker.RETRY_AFTER_S + 1
    asyncio.run(service._cache_write("s1", [], {}))
    assert client.calls == 2, "Redis was never tried again after the window"


def test_the_sse_stream_goes_straight_to_the_disk_poll_while_the_breaker_is_open(clock, monkeypatch):
    from app.main import app
    from app.worker.task_progress import TaskProgressStatus, publish_progress

    connects: list[str] = []

    def refused(url, **kwargs):
        # Recorded rather than asserted inside: the stream's own ``except Exception`` would swallow an AssertionError.
        connects.append(url)
        raise redis.exceptions.ConnectionError("Error 10061 connecting to localhost:6379")

    monkeypatch.setattr(rredis, "from_url", refused)
    task_id = f"breaker-sse-{uuid.uuid4().hex}"
    publish_progress(task_id, TaskProgressStatus.RUNNING, stage="work", message="running", progress=0.5, kind="test")
    redis_breaker.trip()

    def finish_later():
        time.sleep(0.5)
        publish_progress(task_id, TaskProgressStatus.COMPLETED, message="done", progress=1.0, kind="test")

    threading.Thread(target=finish_later, daemon=True).start()
    with TestClient(app).stream("GET", f"/api/tasks/{task_id}/stream") as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())
    assert "RUNNING" in text and "COMPLETED" in text
    assert connects == [], "an SSE stream opened a Redis connection while the breaker was open"

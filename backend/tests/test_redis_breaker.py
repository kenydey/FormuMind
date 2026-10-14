"""One failed Redis connection must not make every later call pay for another (round-4).

The progress store (``worker.task_progress._redis_client``) opened a connection per call and each failure fell back
to disk — with a WARNING and, on Windows, ~2 s per call: every progress event of every background task.
"""
from __future__ import annotations

import sys
import types

import pytest

from app.services import redis_breaker


class _Clock:
    def __init__(self):
        self.now = 500.0

    def __call__(self):
        return self.now


class _Exceptions:
    class ConnectionError(Exception): ...
    class TimeoutError(Exception): ...
    class ResponseError(Exception): ...


@pytest.fixture()
def fake_redis(monkeypatch):
    state = {"built": 0, "fail_with": None, "calls": []}

    class _Client:
        def publish(self, *a):
            state["calls"].append(("publish", a))
            if state["fail_with"]:
                raise state["fail_with"]
            return 1

        value = "plain attribute"

    class _Redis:
        @staticmethod
        def from_url(url, **kwargs):
            state["built"] += 1
            state["kwargs"] = kwargs
            return _Client()

    module = types.SimpleNamespace(Redis=_Redis, exceptions=_Exceptions)
    monkeypatch.setitem(sys.modules, "redis", module)
    clock = _Clock()
    monkeypatch.setattr(redis_breaker, "_monotonic", clock)
    monkeypatch.setattr(redis_breaker, "_open_until", 0.0)
    state["clock"] = clock
    return state


def test_a_failed_call_opens_the_breaker_and_later_clients_are_refused_at_once(fake_redis):
    fake_redis["fail_with"] = _Exceptions.ConnectionError("Error 111")
    client = redis_breaker.client_from_url("redis://x")
    with pytest.raises(_Exceptions.ConnectionError):
        client.publish("ch", "payload")
    assert redis_breaker.is_open()
    built = fake_redis["built"]
    with pytest.raises(_Exceptions.ConnectionError, match="not retrying yet"):
        redis_breaker.client_from_url("redis://x")
    assert fake_redis["built"] == built, "no connection attempt while the breaker is open"


def test_it_closes_again_after_the_window(fake_redis):
    redis_breaker.trip()
    fake_redis["clock"].now += redis_breaker.RETRY_AFTER_S - 1
    with pytest.raises(_Exceptions.ConnectionError):
        redis_breaker.client_from_url("redis://x")
    fake_redis["clock"].now += 2
    assert redis_breaker.client_from_url("redis://x") is not None


def test_timeouts_count_as_unreachable_too(fake_redis):
    fake_redis["fail_with"] = _Exceptions.TimeoutError("timed out")
    with pytest.raises(_Exceptions.TimeoutError):
        redis_breaker.client_from_url("redis://x").publish("ch", "p")
    assert redis_breaker.is_open()


def test_an_error_from_a_reachable_server_does_not_open_it(fake_redis):
    fake_redis["fail_with"] = _Exceptions.ResponseError("WRONGTYPE")
    with pytest.raises(_Exceptions.ResponseError):
        redis_breaker.client_from_url("redis://x").publish("ch", "p")
    assert not redis_breaker.is_open()


def test_a_working_client_is_passed_through(fake_redis):
    client = redis_breaker.client_from_url("redis://x", decode_responses=True)
    assert client.publish("ch", "p") == 1
    assert client.value == "plain attribute"
    assert not redis_breaker.is_open()
    assert fake_redis["kwargs"]["decode_responses"] is True


def test_every_connection_is_bounded(fake_redis):
    redis_breaker.client_from_url("redis://x")
    assert fake_redis["kwargs"]["socket_connect_timeout"] == redis_breaker.CONNECT_TIMEOUT_S
    assert fake_redis["kwargs"]["socket_timeout"] == redis_breaker.SOCKET_TIMEOUT_S


def test_the_progress_store_goes_through_the_breaker(fake_redis, monkeypatch):
    from app.worker import task_progress

    redis_breaker.trip()
    with pytest.raises(_Exceptions.ConnectionError):
        task_progress._redis_client()
    assert fake_redis["built"] == 0


def test_a_task_progress_event_falls_back_to_disk_without_a_second_connection_attempt(fake_redis, tmp_path, monkeypatch):
    from app.worker import task_progress
    from app.worker.task_progress import TaskProgressEvent, TaskProgressStatus

    monkeypatch.setenv("FORMUMIND_TASK_DIR", str(tmp_path))
    fake_redis["fail_with"] = _Exceptions.ConnectionError("Error 111")
    for i in range(6):
        task_progress._store_progress(
            f"t{i}", TaskProgressEvent(status=TaskProgressStatus.RUNNING, stage="s", progress=0.1 * i)
        )
    publishes = [c for c in fake_redis["calls"] if c[0] == "publish"]
    assert len(publishes) == 1, "after the first failure the other five events did not touch Redis"


# ── a slow reply is not an outage; pipelines / locks are guarded ─────────────────────────────────────────────────────

def test_a_timeout_while_reading_a_reply_does_not_open_the_breaker():
    import redis

    assert redis_breaker.is_unreachable(redis.exceptions.TimeoutError("Timeout connecting to server")) is True
    assert redis_breaker.is_unreachable(redis.exceptions.TimeoutError("Timeout reading from socket")) is False
    assert redis_breaker.is_unreachable(redis.exceptions.ConnectionError("refused")) is True
    assert redis_breaker.is_unreachable(redis.exceptions.ResponseError("WRONGTYPE")) is False
    assert redis_breaker.note_failure(redis.exceptions.TimeoutError("Timeout reading from socket")) is False
    assert not redis_breaker.is_open()


def test_objects_returned_by_pipeline_and_lock_trip_the_breaker_too():
    import redis

    class _Pipe:
        def execute(self):
            raise redis.exceptions.ConnectionError("gone")

    class _Client:
        def pipeline(self):
            return _Pipe()

    redis_breaker.reset()
    guarded = redis_breaker.GuardedRedis(_Client())
    with pytest.raises(redis.exceptions.ConnectionError):
        guarded.pipeline().execute()
    assert redis_breaker.is_open()
    redis_breaker.reset()

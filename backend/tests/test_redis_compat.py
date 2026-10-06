"""Every Redis connection names its protocol, so a Redis older than 6.0 still works (round-5; found by the Windows stack smoke test).

redis-py 8 opens every connection with ``HELLO 3``; Redis answers it from 6.0 on. The Redis that runs natively on Windows (the 3.0.504
and 5.0.14.1 ports) does not, and neither does the one Ubuntu 20.04 ships. In CI the Celery worker looped on ``unknown command
`HELLO```, every upload was answered 503, and ``/health`` still said the broker was reachable. These tests run against a small server
that behaves exactly so - refuses HELLO, speaks RESP2 - which needs no Redis installed, and a real Redis 7 behind a front that refused HELLO
was used to check the whole stack (API + Celery worker + upload) by hand.
"""
from __future__ import annotations

import ast
import asyncio
import socketserver
import threading
from pathlib import Path

import pytest
import redis
from app import redis_compat
from app.services import redis_breaker

APP = Path(__file__).resolve().parents[1] / "app"


class _OldRedis(socketserver.ThreadingTCPServer):
    """RESP2 only, no HELLO: enough commands for clients to connect, ping, and keep a hash."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.hashes: dict[str, dict[str, str]] = {}
        self.refused_hello = 0
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"redis://127.0.0.1:{self.server_address[1]}/0"


class _Handler(socketserver.StreamRequestHandler):
    def _command(self):
        head = self.rfile.readline()
        if not head:
            return None
        if not head.startswith(b"*"):
            return head.split()
        args = []
        for _ in range(int(head[1:])):
            size = int(self.rfile.readline()[1:])
            args.append(self.rfile.read(size + 2)[:-2])
        return args

    @staticmethod
    def _bulk(value: str) -> bytes:
        raw = value.encode()
        return b"$%d\r\n%s\r\n" % (len(raw), raw)

    def handle(self):
        server: _OldRedis = self.server  # type: ignore[assignment]
        while (args := self._command()) is not None:
            if not args:
                continue
            name = args[0].upper()
            if name == b"HELLO":
                server.refused_hello += 1
                reply = b"-ERR unknown command `HELLO`, with args beginning with: `3`, \r\n"
            elif name == b"PING":
                reply = b"+PONG\r\n"
            elif name in (b"SELECT", b"CLIENT", b"AUTH"):
                reply = b"+OK\r\n"
            elif name == b"INFO":
                reply = self._bulk("redis_version:5.0.14.1\r\n")
            elif name == b"HSET":
                key = args[1].decode()
                pairs = args[2:]
                server.hashes.setdefault(key, {}).update({pairs[i].decode(): pairs[i + 1].decode() for i in range(0, len(pairs), 2)})
                reply = b":%d\r\n" % (len(pairs) // 2)
            elif name == b"HGETALL":
                flat = [x for kv in server.hashes.get(args[1].decode(), {}).items() for x in kv]
                reply = b"*%d\r\n" % len(flat) + b"".join(self._bulk(x) for x in flat)
            elif name == b"DEL":
                reply = b":%d\r\n" % int(server.hashes.pop(args[1].decode(), None) is not None)
            else:
                reply = b"-ERR unknown command\r\n"
            self.wfile.write(reply)
            self.wfile.flush()


@pytest.fixture()
def old_redis():
    server = _OldRedis()
    yield server
    server.shutdown()
    server.server_close()


# ── the setting and the helpers ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def setting(monkeypatch):
    from app.config import get_settings

    def set_to(value):
        monkeypatch.setenv("FORMUMIND_REDIS_PROTOCOL", str(value))
        get_settings.cache_clear()

    yield set_to
    monkeypatch.undo()
    get_settings.cache_clear()


def test_the_default_is_resp2_which_every_redis_speaks(monkeypatch):
    from app.config import get_settings

    monkeypatch.delenv("FORMUMIND_REDIS_PROTOCOL", raising=False)
    get_settings.cache_clear()
    assert redis_compat.protocol() == 2 and redis_compat.client_kwargs() == {"protocol": 2}
    get_settings.cache_clear()


def test_resp3_is_opt_in_and_nonsense_falls_back_to_resp2(setting):
    setting(3)
    assert redis_compat.protocol() == 3
    setting(7)
    assert redis_compat.protocol() == 2


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("redis://localhost:6379/0", "redis://localhost:6379/0?protocol=2"),
        ("rediss://u:p@host:6380/1", "rediss://u:p@host:6380/1?protocol=2"),
        ("redis://localhost:6379/0?socket_timeout=3", "redis://localhost:6379/0?socket_timeout=3&protocol=2"),
        ("redis://localhost:6379/0?protocol=3", "redis://localhost:6379/0?protocol=3"),  # an explicit choice stays
        ("redis+socket:///var/run/redis.sock", "redis+socket:///var/run/redis.sock"),
        ("memory://", "memory://"),
    ],
)
def test_a_url_is_given_the_protocol_unless_it_names_one_or_is_not_a_redis_url(url, expected):
    assert redis_compat.with_protocol(url) == expected


# ── against a server that does not know HELLO ─────────────────────────────────────────────────────


def test_redis_py_8_as_it_comes_fails_against_such_a_server(old_redis):
    """The failure mode, pinned: if this stops failing, redis-py changed and the module may be unnecessary."""
    with pytest.raises(redis.exceptions.ResponseError, match="unknown command `HELLO`"):
        redis.Redis.from_url(old_redis.url).ping()


def test_a_client_that_names_the_protocol_connects_and_still_gets_dicts_back(old_redis):
    client = redis.Redis.from_url(old_redis.url, decode_responses=True, **redis_compat.client_kwargs())
    assert client.ping() is True
    client.hset("task:1", mapping={"status": "queued", "kind": "file_ingest"})
    assert client.hgetall("task:1") == {"status": "queued", "kind": "file_ingest"}  # a flat list when only the wire was forced to RESP2
    assert old_redis.refused_hello == 0


def test_the_breaker_guarded_client_the_task_registry_uses_does_the_same(old_redis):
    client = redis_breaker.client_from_url(old_redis.url, decode_responses=True)
    client.hset("task:2", mapping={"status": "completed"})
    assert client.hgetall("task:2") == {"status": "completed"}
    assert old_redis.refused_hello == 0


def test_the_asyncio_client_the_progress_stream_uses_connects_too(old_redis):
    import redis.asyncio as aioredis

    async def ping():
        client = aioredis.from_url(old_redis.url, decode_responses=True, **redis_compat.client_kwargs())
        try:
            return await client.ping()
        finally:
            await client.aclose()

    assert asyncio.run(ping()) is True and old_redis.refused_hello == 0


def test_kombu_needs_the_transport_that_names_the_protocol(old_redis):
    from kombu import Connection

    with pytest.raises(Exception, match="unknown command `HELLO`"):
        with Connection(old_redis.url, connect_timeout=3) as plain:
            plain.connect()
            plain.channel()  # opening a channel is what talks to the server (one attempt, no retry loop)

    with Connection(old_redis.url, transport="app.worker.kombu_resp2:Transport", connect_timeout=3) as connection:
        connection.ensure_connection(max_retries=1)
        assert connection.default_channel.client.ping() is True
    assert redis_compat.protocol() == 2


def test_the_celery_app_uses_that_transport_and_a_backend_url_with_the_protocol():
    from app.worker.celery_app import celery_app

    assert celery_app.conf.broker_transport == "app.worker.kombu_resp2:Transport"
    assert "protocol=2" in str(celery_app.conf.result_backend)


def test_a_non_redis_broker_url_keeps_celeries_own_transport():
    assert redis_compat.uses_url_protocol("redis://localhost:6379/0") and not redis_compat.uses_url_protocol("redis+socket:///x")
    assert not redis_compat.uses_url_protocol("memory://")


# ── no client is built without the protocol ───────────────────────────────────────────────────────

_CONSTRUCTORS = {"from_url", "Redis", "StrictRedis", "ConnectionPool", "BlockingConnectionPool"}
_REDIS_NAMES = {"redis", "aioredis", "rredis"}


def _redis_client_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _CONSTRUCTORS:
            root = node.func.value
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id in _REDIS_NAMES:
                yield node


def _names_the_protocol(call: ast.Call) -> bool:
    for keyword in call.keywords:
        if keyword.arg == "protocol":
            return True
        if keyword.arg is None and isinstance(keyword.value, ast.Call) and getattr(keyword.value.func, "id", "") == "client_kwargs":
            return True
    return False


def test_no_redis_client_in_the_application_is_built_without_naming_its_protocol():
    """A new ``redis.Redis.from_url(...)`` that forgets it would work on Redis 7 and fail on every older one, in CI of nobody."""
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in _redis_client_calls(tree):
            # client_from_url adds the protocol itself, for every caller that goes through it
            enclosing = next((f.name for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and call in ast.walk(f)), "")
            if enclosing != "client_from_url" and not _names_the_protocol(call):
                offenders.append(f"{path.relative_to(APP.parent)}:{call.lineno}")
    assert not offenders, "name the protocol (**client_kwargs()) at: " + ", ".join(offenders)

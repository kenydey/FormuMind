"""Which Redis protocol this application speaks, and how to say so to every client.

redis-py 8 negotiates RESP3 on every connection (``HELLO 3``), which a server answers only from Redis 6.0 on. The Redis a
Windows user can install natively is older - Microsoft's port is 3.0.504, the community port everybody finds is 5.0.14.1 - and so
is the one Ubuntu 20.04 (5.0.7) and Debian 10 ship. Against any of them the Celery worker loops on
``unknown command `HELLO```, the API answers 503 to every upload, and ``/health`` still says the broker is reachable (it only
opens a socket). Found by ``scripts/windows/smoke-stack.ps1`` on a real Redis for Windows in CI; reproduced in
``tests/test_redis_compat.py`` against a server that does not know HELLO.

Nothing here uses what RESP3 adds (client-side caching, push messages), so the default is RESP2, which every server speaks;
``FORMUMIND_REDIS_PROTOCOL=3`` opts back in.

The protocol is passed to each client **explicitly** (``protocol=2``), never by changing redis-py's default. A client built
without one decodes replies with the callbacks of the *default* protocol: forcing RESP2 on the wire underneath it made
``HGETALL`` come back as a flat list (found the same way, one step later). With an explicit protocol the parser and the
callbacks agree.

* app clients: ``client_kwargs()`` (``redis.Redis.from_url(url, **client_kwargs())``), or ``redis_breaker.client_from_url``;
* libraries that read connection options from a URL (Celery's result backend): ``with_protocol(url)``;
* the Celery broker, which kombu builds without a protocol: ``app.worker.kombu_resp2.Transport``.
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_URL_SCHEMES = {"redis", "rediss"}


def protocol() -> int:
    """2 unless ``FORMUMIND_REDIS_PROTOCOL`` says 3 (anything else, or unreadable settings: 2)."""
    try:
        from .config import get_settings

        value = int(get_settings().redis_protocol)
    except Exception:  # noqa: BLE001 - a client must still be buildable when settings are not
        return 2
    return value if value in (2, 3) else 2


def client_kwargs() -> dict[str, int]:
    """Keyword arguments that make a redis-py client (sync or asyncio) speak the configured protocol."""
    return {"protocol": protocol()}


def uses_url_protocol(url: str) -> bool:
    return urlsplit(url).scheme in _URL_SCHEMES


def with_protocol(url: str) -> str:
    """``url`` with ``protocol=N`` in its query unless it already names one; other schemes (socket, memory) are left alone."""
    parts = urlsplit(url)
    if parts.scheme not in _URL_SCHEMES:
        return url
    query = parse_qsl(parts.query, keep_blank_values=True)
    if any(key == "protocol" for key, _ in query):
        return url
    query.append(("protocol", str(protocol())))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

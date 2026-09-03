"""Simple in-memory per-IP rate limiting for expensive API endpoints."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

# (method, path_prefix) -> (max_requests, window_seconds)
# NOTE: order matters — the first matching prefix wins, so list more specific
# paths (e.g. /api/research/deep) before their parents (e.g. /api/research).
_RATE_RULES: tuple[tuple[str, str, int, float], ...] = (
    ("POST", "/api/search/stream", 20, 60.0),
    ("POST", "/api/search", 30, 60.0),
    ("POST", "/api/research/deep", 3, 300.0),
    ("POST", "/api/dependencies/install", 5, 300.0),
    ("POST", "/api/settings/secrets", 20, 60.0),
    ("POST", "/api/settings", 20, 60.0),
    ("POST", "/api/ingest/batch", 15, 60.0),
    ("POST", "/api/ingest/url", 20, 60.0),
    ("POST", "/api/ingest", 30, 60.0),
    ("POST", "/api/chat", 10, 60.0),
    ("POST", "/api/formulations/recommend", 10, 60.0),
    ("POST", "/api/doe", 20, 60.0),
    ("POST", "/api/optimize", 5, 60.0),
    ("POST", "/api/research", 5, 120.0),
    ("POST", "/api/intent/parse", 20, 60.0),
    ("GET", "/api/chemical/lookup", 20, 60.0),
    ("POST", "/api/kg/retrieve", 20, 60.0),
    ("POST", "/api/kb/reindex", 3, 300.0),
    ("POST", "/api/experiments/import-csv", 10, 60.0),
)

_buckets: dict[tuple[str, str, str], deque[float]] = defaultdict(deque)
_lock = threading.Lock()
_request_counter = 0
_GC_INTERVAL = 100


def _client_ip(request: Request) -> str:
    # Never trust X-Forwarded-For: it is client-controllable and would let an
    # attacker rotate the rate-limit key at will. Use the direct peer host.
    if request.client:
        return request.client.host
    return "unknown"


def _rule_for(request: Request) -> tuple[int, float, str] | None:
    """Return (limit, window, matched_prefix) so the key uses the prefix
    rather than the full path (prevents path-variant bypass)."""
    path = request.url.path
    method = request.method.upper()
    for rule_method, prefix, limit, window in _RATE_RULES:
        if method == rule_method and (path == prefix or path.startswith(f"{prefix}/")):
            return limit, window, prefix
    return None


def _gc_empty_buckets() -> None:
    """Drop empty deques so the bucket dict does not grow unbounded."""
    empty = [k for k, v in _buckets.items() if not v]
    for k in empty:
        del _buckets[k]


def _allow(key: tuple[str, str, str], limit: int, window: float) -> bool:
    global _request_counter
    now = time.monotonic()
    cutoff = now - window
    with _lock:
        bucket = _buckets[key]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        _request_counter += 1
        if _request_counter % _GC_INTERVAL == 0:
            _gc_empty_buckets()
        return True


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        rule = _rule_for(request)
        if rule is None:
            return await call_next(request)
        limit, window, prefix = rule
        ip = _client_ip(request)
        key = (ip, request.method.upper(), prefix)
        if not _allow(key, limit, window):
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded — try again later"},
                headers={"Retry-After": str(int(window))},
            )
        return await call_next(request)


def reset_rate_limits() -> None:
    """Test helper — clear in-memory counters."""
    global _request_counter
    with _lock:
        _buckets.clear()
        _request_counter = 0

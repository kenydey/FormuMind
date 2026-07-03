"""Simple in-memory per-IP rate limiting for expensive API endpoints."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

# (method, path_prefix) -> (max_requests, window_seconds)
_RATE_RULES: tuple[tuple[str, str, int, float], ...] = (
    ("POST", "/api/search", 30, 60.0),
    ("POST", "/api/search/stream", 20, 60.0),
    ("POST", "/api/research/deep", 10, 60.0),
    ("POST", "/api/dependencies/install", 5, 300.0),
    ("POST", "/api/settings", 20, 60.0),
    ("POST", "/api/settings/secrets", 20, 60.0),
    ("POST", "/api/ingest", 30, 60.0),
    ("POST", "/api/ingest/batch", 15, 60.0),
    ("POST", "/api/ingest/url", 20, 60.0),
)

_buckets: dict[tuple[str, str, str], deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _rule_for(request: Request) -> tuple[int, float] | None:
    path = request.url.path
    method = request.method.upper()
    for rule_method, prefix, limit, window in _RATE_RULES:
        if method == rule_method and (path == prefix or path.startswith(f"{prefix}/")):
            return limit, window
    return None


def _allow(key: tuple[str, str, str], limit: int, window: float) -> bool:
    now = time.monotonic()
    cutoff = now - window
    with _lock:
        bucket = _buckets[key]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        rule = _rule_for(request)
        if rule is None:
            return await call_next(request)
        limit, window = rule
        ip = _client_ip(request)
        key = (ip, request.method.upper(), request.url.path.split("?")[0])
        if not _allow(key, limit, window):
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded — try again later"},
                headers={"Retry-After": str(int(window))},
            )
        return await call_next(request)


def reset_rate_limits() -> None:
    """Test helper — clear in-memory counters."""
    with _lock:
        _buckets.clear()

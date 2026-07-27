"""Bearer API token authentication — enabled by default for public deployments."""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)

_TOKEN_PATH = Path("data/.api_token")
_DEV_TOKEN_CACHE: str | None = None

# Paths reachable without a token (health probes, OpenAPI docs).
_PUBLIC_PREFIXES: tuple[str, ...] = (
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/auth/status",
)
# Public paths matched exactly only (sub-paths still require auth), so that
# /health/detailed (which exposes infra details) stays behind auth.
_PUBLIC_EXACT: tuple[str, ...] = (
    "/health",
)


def _is_public_path(path: str) -> bool:
    if path in _PUBLIC_EXACT:
        return True
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in _PUBLIC_PREFIXES)


def resolve_api_token(settings: Settings) -> str | None:
    """Return the active API token, or None when auth is disabled."""
    global _DEV_TOKEN_CACHE
    if not settings.api_auth_enabled:
        return None
    if settings.api_token:
        return settings.api_token.strip()
    env = settings.environment.strip().lower()
    if env in ("production", "prod"):
        raise RuntimeError(
            "FORMUMIND_API_TOKEN is required when FORMUMIND_API_AUTH_ENABLED=true in production"
        )
    if _DEV_TOKEN_CACHE:
        return _DEV_TOKEN_CACHE
    _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Atomic create-with-O_EXCL avoids the race where two workers both write a
    # new token and clobber each other. If the file already exists, reuse it.
    token = secrets.token_urlsafe(32)
    try:
        fd = os.open(
            str(_TOKEN_PATH),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            os.write(fd, (token + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(_TOKEN_PATH, 0o600)
        _DEV_TOKEN_CACHE = token
        logger.warning(
            "API auth: generated development token at %s — set FORMUMIND_API_TOKEN to override",
            _TOKEN_PATH,
        )
        return token
    except FileExistsError:
        existing = _TOKEN_PATH.read_text(encoding="utf-8").strip()
        if existing:
            os.chmod(_TOKEN_PATH, 0o600)
            _DEV_TOKEN_CACHE = existing
            logger.warning(
                "API auth: using dev token from %s (set FORMUMIND_API_TOKEN for a stable secret)",
                _TOKEN_PATH,
            )
            return existing
        # Empty file from a failed write: fall through and retry next call.
        return None


def reset_dev_token_cache() -> None:
    """Test helper — clear cached auto-generated dev token."""
    global _DEV_TOKEN_CACHE
    _DEV_TOKEN_CACHE = None


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _extract_token(request: Request) -> str | None:
    """Bearer header, or ?token= on GET task stream endpoints (EventSource cannot set headers)."""
    bearer = _extract_bearer(request)
    if bearer:
        return bearer
    # EventSource (browser SSE) cannot set Authorization headers, so the task
    # progress stream accepts ?token=. Restrict to the known task stream path
    # to avoid leaking the token to unrelated /stream endpoints.
    path = request.url.path
    if (
        request.method == "GET"
        and path.startswith("/api/tasks/")
        and path.endswith("/stream")
    ):
        query_token = request.query_params.get("token")
        if query_token:
            return query_token.strip()
    return None


class ApiAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # CORS preflight must reach CORSMiddleware (outer layer); never 401.
        if request.method == "OPTIONS":
            return await call_next(request)
        settings = get_settings()
        if not settings.api_auth_enabled or _is_public_path(request.url.path):
            return await call_next(request)
        token = resolve_api_token(settings)
        if token is None:
            return await call_next(request)
        provided = _extract_token(request)
        if not provided or not secrets.compare_digest(provided, token):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API token"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


def install_api_auth(app) -> None:
    app.add_middleware(ApiAuthMiddleware)

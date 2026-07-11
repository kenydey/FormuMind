"""Bearer API token authentication — enabled by default for public deployments."""
from __future__ import annotations

import logging
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
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/auth/status",
)


def _is_public_path(path: str) -> bool:
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
    if _TOKEN_PATH.is_file():
        token = _TOKEN_PATH.read_text(encoding="utf-8").strip()
        if token:
            _DEV_TOKEN_CACHE = token
            logger.warning(
                "API auth: using dev token from %s (set FORMUMIND_API_TOKEN for a stable secret)",
                _TOKEN_PATH,
            )
            return token
    token = secrets.token_urlsafe(32)
    _TOKEN_PATH.write_text(token + "\n", encoding="utf-8")
    _DEV_TOKEN_CACHE = token
    logger.warning(
        "API auth: generated development token at %s — set FORMUMIND_API_TOKEN to override",
        _TOKEN_PATH,
    )
    return token


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
    """Bearer header, or ?token= on GET stream endpoints (EventSource cannot set headers)."""
    bearer = _extract_bearer(request)
    if bearer:
        return bearer
    if request.method == "GET" and request.url.path.endswith("/stream"):
        query_token = request.query_params.get("token")
        if query_token:
            return query_token.strip()
    return None


class ApiAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        settings = get_settings()
        if not settings.api_auth_enabled or _is_public_path(request.url.path):
            return await call_next(request)
        try:
            token = resolve_api_token(settings)
        except RuntimeError as exc:
            # Misconfiguration (e.g. auth enabled in production without a token):
            # surface a clear 503 instead of an opaque 500 on every request.
            return JSONResponse(status_code=503, content={"detail": str(exc)})
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

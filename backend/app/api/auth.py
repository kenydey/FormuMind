"""Public auth status — no bearer token required."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..config import get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/status")
def auth_status(request: Request = None) -> dict:  # type: ignore[no-untyped-def]
    """Tell the SPA whether API bearer auth is enforced (safe without a token)."""
    import os

    settings = get_settings()
    owner = "default"
    if request is not None:
        try:
            from ..middleware.api_auth import get_current_owner

            owner = get_current_owner(request)
        except Exception:
            owner = "default"
    return {
        "auth_required": settings.api_auth_enabled,
        "multi_user": os.getenv("FORMUMIND_MULTI_USER", "").strip().lower() in ("1", "true", "yes"),
        "owner": owner,
        "hint": (
            "Configure FORMUMIND_API_TOKEN on the server and enter the same token "
            "in Settings → API 访问令牌 (runtime only; do not bake into the frontend build)."
            if settings.api_auth_enabled
            else ""
        ),
    }

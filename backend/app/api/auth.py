"""Public auth status — no bearer token required."""
from __future__ import annotations

from fastapi import APIRouter

from ..config import get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/status")
def auth_status() -> dict:
    """Tell the SPA whether API bearer auth is enforced (safe without a token)."""
    settings = get_settings()
    return {
        "auth_required": settings.api_auth_enabled,
        "hint": (
            "Configure FORMUMIND_API_TOKEN on the server and enter the same token in Settings, "
            "or set VITE_API_TOKEN when building the frontend."
            if settings.api_auth_enabled
            else ""
        ),
    }

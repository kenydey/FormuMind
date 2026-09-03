"""NotebookLM authorization & runtime config.

GET  /api/notebooklm/auth-status — granular setup status for the auth UI.
POST /api/notebooklm/config       — set enabled + notebook_id at runtime (no env edit).
POST /api/notebooklm/login        — trigger the one-time `notebooklm login` browser
     auth on the backend machine, or return a manual fallback when headless.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..services import notebooklm

router = APIRouter(prefix="/api", tags=["notebooklm"])


class NotebookLMConfig(BaseModel):
    enabled: bool | None = None
    notebook_id: str | None = None


@router.get("/notebooklm/auth-status")
def auth_status() -> dict:
    return notebooklm.get_setup_status()


@router.post("/notebooklm/config")
def set_config(cfg: NotebookLMConfig) -> dict:
    return notebooklm.set_runtime_config(
        enabled=cfg.enabled, notebook_id=cfg.notebook_id
    )


@router.post("/notebooklm/login")
def login() -> dict:
    return notebooklm.start_login()

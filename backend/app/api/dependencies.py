"""GET/POST /api/dependencies — optional-dependency status + install / upgrade."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..config import get_settings
from ..middleware.api_auth import get_current_owner
from ..services import dependencies as deps
from ..worker.tasks import run_deps_install_task
from ._dispatch import submit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["dependencies"])


class DependencyAction(BaseModel):
    names: list[str]
    upgrade: bool = False


@router.get("/dependencies")
def list_dependencies() -> dict:
    settings = get_settings()
    return {
        "dependencies": deps.status(),
        "online_core_missing": deps.online_core_missing(),
        "install_enabled": bool(settings.deps_install_enabled),
    }


@router.post("/dependencies/install", status_code=202)
def install_dependencies(action: DependencyAction, request: Request) -> JSONResponse:
    settings = get_settings()
    if not settings.deps_install_enabled:
        raise HTTPException(
            status_code=403,
            detail=(
                "Server-side pip install is disabled "
                "(set FORMUMIND_DEPS_INSTALL_ENABLED=true to allow)"
            ),
        )
    if not action.names:
        raise HTTPException(status_code=400, detail="未选择任何依赖")
    try:
        deps.validate_names(action.names)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    owner = get_current_owner(request)
    logger.warning(
        "deps install accepted: owner=%s names=%s upgrade=%s",
        owner,
        action.names,
        action.upgrade,
    )
    return submit(run_deps_install_task, {
        "names": action.names,
        "upgrade": action.upgrade,
    }, "deps")

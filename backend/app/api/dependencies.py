"""GET/POST /api/dependencies — optional-dependency status + install / upgrade."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..services import dependencies as deps
from ..worker.tasks import run_deps_install_task
from .tasks import accepted_response

router = APIRouter(prefix="/api", tags=["dependencies"])


class DependencyAction(BaseModel):
    names: list[str]
    upgrade: bool = False


@router.get("/dependencies")
def list_dependencies() -> dict:
    return {
        "dependencies": deps.status(),
        "online_core_missing": deps.online_core_missing(),
    }


@router.post("/dependencies/install", status_code=202)
def install_dependencies(action: DependencyAction) -> JSONResponse:
    if not action.names:
        raise HTTPException(status_code=400, detail="未选择任何依赖")
    try:
        deps.validate_names(action.names)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    async_result = run_deps_install_task.delay({
        "names": action.names,
        "upgrade": action.upgrade,
    })
    return accepted_response(async_result.id, "deps")

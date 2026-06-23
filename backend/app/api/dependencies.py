"""GET/POST /api/dependencies — optional-dependency status + install / upgrade.

The Settings UI uses these to show which optional libraries are present and to
install or upgrade the ones that unlock online mode. Installs run in the
background (they can take minutes) and are polled via ``GET /api/tasks/{id}``.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import dependencies as deps
from ..worker.tasks import task_manager

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


@router.post("/dependencies/install")
def install_dependencies(action: DependencyAction) -> dict:
    if not action.names:
        raise HTTPException(status_code=400, detail="未选择任何依赖")
    try:
        deps.validate_names(action.names)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    task_id = task_manager.submit_dependency_install(action.names, upgrade=action.upgrade)
    return {"task_id": task_id, "poll_url": f"/api/tasks/{task_id}"}

"""Task polling endpoint for long-running async jobs."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..domain.schemas import TaskStatus
from ..worker.tasks import load_persisted_task, task_manager

router = APIRouter(prefix="/api", tags=["tasks"])


@router.get("/tasks/{task_id}", response_model=TaskStatus)
def get_task(task_id: str) -> TaskStatus:
    status = task_manager.get(task_id)
    if status is None:
        # Fallback: dep-install tasks persist their result to disk so a
        # uvicorn --reload (triggered by pip writes to .venv) doesn't 404.
        status = load_persisted_task(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Unknown task id")
    return status

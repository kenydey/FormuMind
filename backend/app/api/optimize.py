"""Optimize endpoint: kick off the async closed-loop formulation optimizer."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..domain.schemas import Requirement
from ..worker.tasks import task_manager

router = APIRouter(prefix="/api", tags=["optimize"])


class OptimizeRequest(BaseModel):
    requirement: Requirement
    iterations: int | None = None


class TaskHandle(BaseModel):
    task_id: str
    poll_url: str


@router.post("/optimize", response_model=TaskHandle)
def start_optimization(payload: OptimizeRequest) -> TaskHandle:
    task_id = task_manager.submit_optimization(payload.requirement, payload.iterations)
    return TaskHandle(task_id=task_id, poll_url=f"/api/tasks/{task_id}")

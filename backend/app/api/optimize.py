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
    engine: str = "auto"
    campaign_state: str | None = None
    workbench_campaign_id: int | None = None


class TaskHandle(BaseModel):
    task_id: str
    poll_url: str


@router.post("/optimize", response_model=TaskHandle)
def start_optimization(payload: OptimizeRequest) -> TaskHandle:
    task_id = task_manager.submit_optimization(
        payload.requirement,
        payload.iterations,
        engine=payload.engine,
        campaign_state=payload.campaign_state,
        workbench_campaign_id=payload.workbench_campaign_id,
    )
    return TaskHandle(task_id=task_id, poll_url=f"/api/tasks/{task_id}")

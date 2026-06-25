"""Optimize endpoint: async Celery closed-loop optimizer."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..domain.schemas import Requirement
from ..worker.tasks import run_optimize_task
from .tasks import accepted_response

router = APIRouter(prefix="/api", tags=["optimize"])


class OptimizeRequest(BaseModel):
    requirement: Requirement
    iterations: int | None = None
    engine: str = "auto"
    campaign_state: str | None = None
    workbench_campaign_id: int | None = None


@router.post("/optimize", status_code=202)
def start_optimization(payload: OptimizeRequest) -> JSONResponse:
    async_result = run_optimize_task.delay({
        "requirement": payload.requirement.model_dump(),
        "iterations": payload.iterations,
        "engine": payload.engine,
        "campaign_state": payload.campaign_state,
        "workbench_campaign_id": payload.workbench_campaign_id,
    })
    return accepted_response(async_result.id, "optimize")

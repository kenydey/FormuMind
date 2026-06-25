"""Self-driving loop endpoint — Celery + SSE."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..domain.schemas import LoopRequest, Requirement
from ..worker.tasks import run_loop_task
from .tasks import accepted_response

router = APIRouter(prefix="/api", tags=["loop"])


@router.post("/loop/iterate", status_code=202)
def iterate_loop(payload: LoopRequest) -> JSONResponse:
    req = Requirement(
        **payload.model_dump(
            exclude={"optimize_iterations", "n_suggest", "optimize_engine", "doe_engine"}
        )
    )
    async_result = run_loop_task.delay({
        "requirement": req.model_dump(),
        "iterations": payload.optimize_iterations,
        "n_suggest": payload.n_suggest,
        "optimize_engine": payload.optimize_engine,
        "doe_engine": payload.doe_engine,
    })
    return accepted_response(async_result.id, "loop")

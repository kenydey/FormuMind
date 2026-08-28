"""Self-driving loop endpoint — Celery + SSE."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from ..domain.schemas import LoopRequest, Requirement
from ..worker.tasks import run_loop_task
from ._dispatch import submit

router = APIRouter(prefix="/api", tags=["loop"])


@router.post("/loop/iterate", status_code=202)
def iterate_loop(payload: LoopRequest, request: Request) -> JSONResponse:
    from ..middleware.api_auth import get_current_owner

    req = Requirement(
        **payload.model_dump(
            exclude={
                "optimize_iterations",
                "n_suggest",
                "optimize_engine",
                "doe_engine",
                "workbench_campaign_id",
                "campaign_state",
                "prior_rmse_history",
                "prior_optimization",
                "prior_next_doe",
                "budget_remaining",
            }
        )
    )
    return submit(run_loop_task, {
        "requirement": req.model_dump(),
        "iterations": payload.optimize_iterations,
        "n_suggest": payload.n_suggest,
        "optimize_engine": payload.optimize_engine,
        "doe_engine": payload.doe_engine,
        "workbench_campaign_id": payload.workbench_campaign_id,
        "campaign_state": payload.campaign_state,
        "prior_rmse_history": payload.prior_rmse_history,
        "prior_optimization": payload.prior_optimization.model_dump()
        if payload.prior_optimization
        else None,
        "prior_next_doe": payload.prior_next_doe.model_dump() if payload.prior_next_doe else None,
        "budget_remaining": payload.budget_remaining,
    }, "loop", owner_id=get_current_owner(request))

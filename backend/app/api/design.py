"""Inverse design endpoint: target properties → Pareto set of formulations.

Async because the search evaluates thousands of candidates. Mirrors the
optimize/loop endpoints: durable outbox row, Celery dispatch, 202 with an SSE
stream URL.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..domain.schemas import Requirement, TargetSpec
from ..worker.tasks import run_inverse_design_task
from ._idempotency import enqueue_outbox
from .tasks import accepted_response

router = APIRouter(prefix="/api", tags=["design"])


class InverseDesignBody(BaseModel):
    requirement: Requirement
    targets: TargetSpec = Field(default_factory=TargetSpec)
    population: int = Field(default=48, ge=8, le=400)
    generations: int = Field(default=30, ge=1, le=300)
    # LLM proposals seed the initial population with compositions no template
    # contains. Off falls back to baseline + randomised variants.
    seed_with_llm: bool = True
    seed: int = 42


@router.post("/design/inverse", status_code=202)
def start_inverse_design(body: InverseDesignBody) -> JSONResponse:
    """Search formulation space for candidates meeting the target spec."""
    payload = {
        "requirement": body.requirement.model_dump(),
        "targets": body.targets.model_dump(),
        "population": body.population,
        "generations": body.generations,
        "seed_with_llm": body.seed_with_llm,
        "seed": body.seed,
    }
    outbox_id = enqueue_outbox("inverse_design", payload)
    async_result = run_inverse_design_task.delay(payload)
    return accepted_response(async_result.id, "inverse_design", outbox_id=outbox_id)

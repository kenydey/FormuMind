"""Session Plan API — structured plans for long tasks (DOE design, multi-round optimization).

Auth is enforced by the global bearer-token middleware (see middleware/api_auth),
same as the other routers; no per-endpoint auth code here.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import session_plan as plan_svc

router = APIRouter(prefix="/api/session-plans", tags=["session-plans"])


class PhaseIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    steps: list[str] = Field(..., min_length=1, max_length=64)


class SubmitPlanBody(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    phases: list[PhaseIn] = Field(..., min_length=1, max_length=32)


class DecideBody(BaseModel):
    approved: bool
    actor: str | None = Field(default=None, max_length=128)


class AdvanceBody(BaseModel):
    phase_name: str = Field(..., min_length=1, max_length=128)
    step_idx: int = Field(..., ge=0)


def _to_out(plan: plan_svc.Plan) -> dict:
    return plan.to_dict()


@router.post("", response_model=dict)
def submit_plan(body: SubmitPlanBody) -> dict:
    try:
        plan = plan_svc.submit_plan(
            body.session_id,
            [{"name": p.name, "steps": p.steps} for p in body.phases],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_out(plan)


@router.get("/pending", response_model=dict)
def list_pending() -> dict:
    """Plans awaiting approval, oldest first (drives the frontend approval center)."""
    items = plan_svc.list_pending_plans()
    return {"items": items, "total": len(items)}


@router.get("/{plan_id}", response_model=dict)
def get_plan(plan_id: str) -> dict:
    try:
        plan = plan_svc.get_plan(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return _to_out(plan)


@router.post("/{plan_id}/decide", response_model=dict)
def decide_plan(plan_id: str, body: DecideBody) -> dict:
    try:
        plan = plan_svc.decide_plan(plan_id, body.approved, actor=body.actor)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _to_out(plan)


@router.post("/{plan_id}/advance", response_model=dict)
def advance_plan(plan_id: str, body: AdvanceBody) -> dict:
    try:
        plan = plan_svc.advance(plan_id, body.phase_name, body.step_idx)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _to_out(plan)

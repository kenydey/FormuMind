"""Multi-agent review endpoint (v0.8).

POST /api/agents/review runs the supervisor (InitializeAgent), which dispatches
to the Chemist and Inspector agents and returns a single pure-JSON verdict with
interceptions and remediation recommendations.
"""
from __future__ import annotations

from fastapi import APIRouter

from ..agents.supervisor import InitializeAgent
from ..domain.schemas import AgentReviewRequest, ReviewVerdict

router = APIRouter(prefix="/api", tags=["agents"])


@router.post("/agents/review", response_model=ReviewVerdict)
def review(req: AgentReviewRequest) -> ReviewVerdict:
    """Review a formulation for chemical compatibility and regulatory compliance."""
    return InitializeAgent().review(
        req.formulation, requirement=req.requirement, explain=req.explain
    )

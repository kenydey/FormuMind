"""DOE endpoint: generate an experimental design over key formulation levers."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..domain.schemas import DOEPlan, Requirement
from ..pipeline import workflow

router = APIRouter(prefix="/api", tags=["doe"])

DESIGNS = ["full_factorial", "fractional_factorial", "plackett_burman", "ccd", "lhs"]


@router.post("/doe", response_model=DOEPlan)
def generate_doe(
    requirement: Requirement,
    design: str = Query("full_factorial", enum=DESIGNS),
) -> DOEPlan:
    return workflow.build_doe(requirement, design=design)

"""Process parameter optimization API endpoint (v0.5)."""
from __future__ import annotations

from fastapi import APIRouter

from ..domain.schemas import ProcessOptRequest, ProcessOptResult
from ..services.process_optimizer import run_process_optimization

router = APIRouter(prefix="/api", tags=["process"])


@router.post("/process-optimize", response_model=ProcessOptResult)
def process_optimize(req: ProcessOptRequest) -> ProcessOptResult:
    """Optimize manufacturing process parameters for a given product domain.

    Returns the best process parameters (cure temperature, dispersion speed,
    film thickness, etc.) and the predicted performance outcomes.
    """
    return run_process_optimization(req)

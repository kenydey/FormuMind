"""Optimize endpoint: async Celery closed-loop optimizer."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..domain.schemas import Requirement
from ..worker.tasks import run_optimize_task
from ._dispatch import submit

router = APIRouter(prefix="/api", tags=["optimize"])


class OptimizeRequest(BaseModel):
    requirement: Requirement
    iterations: int | None = Field(default=None, ge=1, le=1000)
    engine: str = "auto"
    campaign_state: str | None = None
    workbench_campaign_id: int | None = None
    # v16: DOE 冷启动种子。None = 确定性默认 0；整数 = 可复现。
    # 边界：设计矩阵可复现 ≠ BayBE GP 采样可复现（GP 内部仍走其自身随机性）。
    # v23-fix: description 与注释对齐（v22 遗漏）。
    seed: int | None = Field(default=None, description="DOE cold-start seed; None=deterministic default 0")


@router.post("/optimize", status_code=202)
def start_optimization(payload: OptimizeRequest) -> JSONResponse:
    return submit(run_optimize_task, {
        "requirement": payload.requirement.model_dump(),
        "iterations": payload.iterations,
        "engine": payload.engine,
        "campaign_state": payload.campaign_state,
        "workbench_campaign_id": payload.workbench_campaign_id,
    "seed": payload.seed,
    }, "optimize")

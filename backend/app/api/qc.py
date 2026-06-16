"""POST /api/qc/analyze — coating defect QC (reserved skeleton).

Computer-vision defect classification (cracks, blistering, rust) belongs to the
downstream quality-control stage rather than upstream formulation design, and is
best deployed as an independent microservice (FastAPI + a PyTorch model in its
own Docker image). This endpoint defines the request/response contract so the
frontend can integrate against a stable shape; it returns a placeholder result
until a real model is wired in. Kept intentionally dependency-free.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class QCRequest(BaseModel):
    image_url: str
    notes: str = ""


class Defect(BaseModel):
    label: str  # e.g. "crack", "blister", "rust", "orange_peel"
    confidence: float
    bbox: list[float] = []  # [x, y, w, h] in pixels, optional


class QCResponse(BaseModel):
    defects: list[Defect]
    engine: str = "placeholder"
    message: str = ""


@router.post("/qc/analyze", response_model=QCResponse)
def analyze(req: QCRequest) -> QCResponse:
    """Reserved: returns an empty defect list until a CV model is deployed."""
    return QCResponse(
        defects=[],
        engine="placeholder",
        message="视觉质检为预留接口；未来由独立的 torch/torchvision 微服务提供缺陷检测。",
    )

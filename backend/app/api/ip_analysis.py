"""IP Compliance Analysis API endpoint (v0.5)."""
from __future__ import annotations

from fastapi import APIRouter

from ..domain.schemas import IPAnalysisRequest, IPReport
from ..services.ip_analysis import analyze_ip_risk

router = APIRouter(prefix="/api", tags=["ip"])


@router.post("/ip/analyze", response_model=IPReport)
def analyze_ip(req: IPAnalysisRequest) -> IPReport:
    """Analyze IP landscape for a formulation.

    Searches relevant patents and returns a novelty score, risk list, and
    whitespace hints. Uses an LLM when configured; falls back to offline
    keyword matching otherwise.
    """
    return analyze_ip_risk(req)

"""GET /api/meta/* — static metadata for the frontend (default levers, etc.)."""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..domain.project_spec import default_levers_for
from ..domain.schemas import LeverSpec, ProductDomain, Substrate

router = APIRouter(prefix="/api/meta", tags=["meta"])


class DefaultLeversResponse(BaseModel):
    levers: list[LeverSpec]


@router.get("/default-levers", response_model=DefaultLeversResponse)
def get_default_levers(
    domain: ProductDomain = Query(..., description="Product domain"),
    substrate: Substrate = Query(Substrate.carbon_steel, description="Substrate"),
    cure_temperature_c: float | None = Query(None, description="Cure temp for process levers"),
) -> DefaultLeversResponse:
    """Return default DOE levers for a domain/substrate (SSOT = project_spec.resolve_levers)."""
    levers = default_levers_for(
        domain,
        substrate,
        cure_temperature_c=cure_temperature_c,
    )
    return DefaultLeversResponse(levers=levers)

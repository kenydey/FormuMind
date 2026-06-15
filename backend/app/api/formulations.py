"""Metadata endpoint: domains, substrates, DOE designs, and baseline templates."""
from __future__ import annotations

from fastapi import APIRouter

from ..domain.knowledge import baseline_formulation
from ..domain.schemas import Formulation, ProductDomain, Requirement, Substrate

router = APIRouter(prefix="/api", tags=["metadata"])


@router.get("/meta")
def metadata() -> dict:
    return {
        "domains": [d.value for d in ProductDomain],
        "substrates": [s.value for s in Substrate],
        "designs": ["full_factorial", "fractional_factorial", "plackett_burman", "ccd", "lhs"],
    }


@router.get("/templates/{domain}", response_model=Formulation)
def template(domain: ProductDomain) -> Formulation:
    req = Requirement(domain=domain)
    return baseline_formulation(req)

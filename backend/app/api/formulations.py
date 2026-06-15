"""Metadata endpoint: domains, substrates, DOE designs, and baseline templates."""
from __future__ import annotations

from fastapi import APIRouter

from ..domain.knowledge import RAW_MATERIALS, baseline_formulation
from ..domain.schemas import Formulation, ProductDomain, Requirement, Substrate

router = APIRouter(prefix="/api", tags=["metadata"])


@router.get("/meta")
def metadata() -> dict:
    return {
        "domains": [d.value for d in ProductDomain],
        "substrates": [s.value for s in Substrate],
        "designs": ["full_factorial", "fractional_factorial", "plackett_burman", "ccd", "lhs"],
    }


@router.get("/ingredients")
def ingredients() -> dict:
    """Return the full raw-material library including price and VOC metadata."""
    return {
        name: {
            "role": spec.get("role"),
            "formula": spec.get("formula"),
            "molar_mass": spec.get("molar_mass"),
            "price_cny_per_kg": spec.get("price_cny_per_kg"),
            "voc_contrib": spec.get("voc_contrib"),
        }
        for name, spec in RAW_MATERIALS.items()
    }


@router.get("/templates/{domain}", response_model=Formulation)
def template(domain: ProductDomain) -> Formulation:
    req = Requirement(domain=domain)
    return baseline_formulation(req)

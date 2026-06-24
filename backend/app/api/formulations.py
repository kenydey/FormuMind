"""Metadata endpoint: domains, substrates, DOE designs, and baseline templates."""
from __future__ import annotations

from fastapi import APIRouter

from ..domain.examples import BUILTIN_METRICS, EXAMPLE_PROJECTS, ROLE_CATALOG, load_example
from ..domain.knowledge import RAW_MATERIALS, baseline_formulation
from ..domain.schemas import Formulation, ProductDomain, Requirement, Substrate

router = APIRouter(prefix="/api", tags=["metadata"])


from ..services.engines.pydoe_engine import PYDOE_DESIGNS

_NATIVE_DESIGNS = ["full_factorial", "fractional_factorial", "plackett_burman", "ccd", "lhs"]
_ALL_DESIGNS = _NATIVE_DESIGNS + [d for d in PYDOE_DESIGNS if d not in _NATIVE_DESIGNS]


@router.get("/meta")
def metadata() -> dict:
    return {
        "domains": [d.value for d in ProductDomain],
        "substrates": [s.value for s in Substrate],
        "designs": _ALL_DESIGNS,
        "doe_engines": ["auto", "native", "pydoe"],
        "al_engines": ["auto", "legacy", "baybe"],
        "pydoe_designs": list(PYDOE_DESIGNS),
        "example_projects": [
            {"id": k, "label": v["label"], "domain": v["domain"].value}
            for k, v in EXAMPLE_PROJECTS.items()
        ],
        "builtin_metrics": BUILTIN_METRICS,
        "role_catalog": ROLE_CATALOG,
    }


@router.get("/examples/{example_id}", response_model=Requirement)
def get_example_project(example_id: str) -> Requirement:
    return load_example(example_id)


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

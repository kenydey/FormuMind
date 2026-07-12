"""Chemical lookup API — CAS / name cross-reference and ChemCrow profile."""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..domain.schemas import MaterialSpec
from ..services.chemical_lookup import lookup_chemical
from ..services.chemtools import availability, chemical_profile, enrich_material_specs

router = APIRouter(prefix="/api", tags=["chemistry"])


class EnrichMaterialsRequest(BaseModel):
    materials: list[MaterialSpec] = Field(default_factory=list)


class EnrichMaterialsResponse(BaseModel):
    materials: list[MaterialSpec]
    warnings: list[str] = Field(default_factory=list)


@router.get("/chemical/lookup")
def chemical_lookup(q: str = Query(..., min_length=1, description="中文名/英文名/CAS No.")) -> dict:
    """Look up chemical metadata by name or CAS (PubChem + catalog, 24h cache)."""
    return lookup_chemical(q)


@router.get("/chemical/profile")
def chemical_profile_endpoint(
    q: str = Query(..., min_length=1, description="中文名/英文名/CAS No."),
) -> dict:
    """Full chemical dossier: lookup + functional groups + molecular patent
    pre-screen + controlled/explosive safety flags (ChemCrow tool gateway).

    Superset of ``/chemical/lookup``; ChemCrow-backed fields degrade to
    neutral values when the intel extra is not installed.
    """
    return chemical_profile(q)


@router.get("/chemical/tools")
def chemical_tools_status() -> dict:
    """Availability report for the ChemCrow tool gateway (per capability)."""
    return availability()


@router.post("/chemical/enrich-materials", response_model=EnrichMaterialsResponse)
def enrich_materials_endpoint(req: EnrichMaterialsRequest) -> EnrichMaterialsResponse:
    """Fill missing SMILES on a material list (catalog → ChemCrow) and run a
    controlled-chemical screen. Warnings are advisory — never hard blocks."""
    warnings = enrich_material_specs(req.materials)
    return EnrichMaterialsResponse(materials=req.materials, warnings=warnings)

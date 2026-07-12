"""Chemical lookup API — CAS / name cross-reference and ChemCrow profile."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..services.chemical_lookup import lookup_chemical
from ..services.chemtools import availability, chemical_profile

router = APIRouter(prefix="/api", tags=["chemistry"])


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

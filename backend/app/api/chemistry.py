"""Chemical lookup API — CAS / name cross-reference."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..services.chemical_lookup import lookup_chemical

router = APIRouter(prefix="/api", tags=["chemistry"])


@router.get("/chemical/lookup")
def chemical_lookup(q: str = Query(..., min_length=1, description="中文名/英文名/CAS No.")) -> dict:
    """Look up chemical metadata by name or CAS (PubChem + catalog, 24h cache)."""
    return lookup_chemical(q)

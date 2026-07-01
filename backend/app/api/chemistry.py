"""Chemical lookup API — CAS / name cross-reference with 24h cache."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..services.compounds import lookup_compound

router = APIRouter(prefix="/api", tags=["chemistry"])


@router.get("/chemical/lookup")
def chemical_lookup(q: str = Query(..., min_length=1, description="中文名/英文名/CAS No.")) -> dict:
    """Resolve compound by name or CAS; results cached 24h in-process."""
    return lookup_compound(q)

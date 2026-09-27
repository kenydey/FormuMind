"""Provenance lineage API (W3-10) — read-only upstream BFS over provenance edges.

The underlying service is fail-open: DB errors yield an empty edge list,
while an unknown node type is a 400.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..services import provenance as prov_svc

router = APIRouter(prefix="/api/provenance", tags=["provenance"])


@router.get("/lineage", response_model=dict)
def get_lineage(
    node_type: str = Query(..., min_length=1, max_length=32),
    node_id: str = Query(..., min_length=1, max_length=512),
    depth: int = Query(default=3, ge=1, le=6),
):
    try:
        edges = prov_svc.lineage(node_type, node_id, depth=depth)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "node_type": node_type,
        "node_id": node_id,
        "depth": depth,
        "edges": edges,
    }

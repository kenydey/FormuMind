"""Neo4j-backed KG endpoints (optional sibling to /api/kg).

Mounted at ``/api/kg/neo4j``. Disabled (404) when
``FORMUMIND_NEO4J_ENABLED`` is not truthy OR the driver cannot connect.

These endpoints are an *additive* mirror of the most useful read-side
operations against Neo4j, for callers (admin tooling, dashboards) that
want to confirm what landed in the graph store. They never replace the
SQLite-backed ``/api/kg/`` routes used by the recommendation pipeline.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..services import neo4j_kg

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kg/neo4j", tags=["kg-neo4j"])


class StatsResponse(BaseModel):
    enabled: bool
    reachable: bool
    stats: Dict[str, int]


class CompoundIn(BaseModel):
    uid: str
    name: str
    cas_number: Optional[str] = None
    smiles: Optional[str] = None
    molecular_weight: Optional[float] = None
    supplier: Optional[str] = None
    notes: Optional[str] = None


class FormulationIn(BaseModel):
    uid: str
    name: str
    description: Optional[str] = None
    target_property: Optional[str] = None
    target_value: Optional[float] = None
    status: str = "draft"


class LinkResponse(BaseModel):
    ok: bool
    message: str


class CompoundView(BaseModel):
    uid: str
    name: Optional[str] = None
    cas_number: Optional[str] = None
    ratio: Optional[float] = None


class CompoundStats(BaseModel):
    uid: str
    name: Optional[str] = None
    co_count: int


def _ensure() -> None:
    """Raise 503 if Neo4j adapter is disabled or unreachable."""
    if not neo4j_kg.is_enabled():
        raise HTTPException(
            status_code=503,
            detail="FORMUMIND_NEO4J_ENABLED is false — Neo4j adapter disabled.",
        )
    if not neo4j_kg.healthcheck():
        raise HTTPException(
            status_code=503, detail="Neo4j unreachable (Bolt endpoint down)."
        )


@router.get("/stats", response_model=StatsResponse)
def stats() -> StatsResponse:
    """Return adapter status and a quick node/edge count snapshot."""
    enabled = neo4j_kg.is_enabled()
    reachable = neo4j_kg.healthcheck() if enabled else False
    s: Dict[str, int] = neo4j_kg.get_stats() if reachable else {}
    return StatsResponse(enabled=enabled, reachable=reachable, stats=s)


@router.post("/schema/ensure", response_model=LinkResponse)
def ensure_schema() -> LinkResponse:
    _ensure()
    ok = neo4j_kg.ensure_schema()
    return LinkResponse(
        ok=ok,
        message="constraints created/verified" if ok else "failed to create constraints",
    )


@router.post("/compounds", response_model=LinkResponse)
def upsert_compound(payload: CompoundIn) -> LinkResponse:
    _ensure()
    ok = neo4j_kg.upsert_compound(
        uid=payload.uid,
        name=payload.name,
        cas_number=payload.cas_number,
        smiles=payload.smiles,
        molecular_weight=payload.molecular_weight,
        supplier=payload.supplier,
        notes=payload.notes,
    )
    return LinkResponse(ok=ok, message="upserted" if ok else "failed")


@router.post("/formulations", response_model=LinkResponse)
def upsert_formulation(payload: FormulationIn) -> LinkResponse:
    _ensure()
    ok = neo4j_kg.upsert_formulation(
        uid=payload.uid,
        name=payload.name,
        description=payload.description,
        target_property=payload.target_property,
        target_value=payload.target_value,
        status=payload.status,
    )
    return LinkResponse(ok=ok, message="upserted" if ok else "failed")


@router.post(
    "/formulations/{form_uid}/compounds/{comp_uid}",
    response_model=LinkResponse,
)
def link_formulation_contains(
    form_uid: str,
    comp_uid: str,
    ratio: Optional[float] = Query(None, ge=0.0, le=1.0),
) -> LinkResponse:
    _ensure()
    ok = neo4j_kg.link_formulation_contains(form_uid, comp_uid, ratio=ratio)
    return LinkResponse(ok=ok, message="linked" if ok else "failed")


@router.post(
    "/formulations/{form_a}/similar/{form_b}",
    response_model=LinkResponse,
)
def link_similarity(
    form_a: str, form_b: str, score: float = Query(1.0, ge=0.0, le=1.0)
) -> LinkResponse:
    _ensure()
    ok = neo4j_kg.link_formulation_similarity(form_a, form_b, score=score)
    return LinkResponse(ok=ok, message="linked" if ok else "failed")


@router.get("/formulations/{form_uid}/compounds", response_model=List[CompoundView])
def formulation_compounds(form_uid: str) -> List[Dict[str, Any]]:
    _ensure()
    return neo4j_kg.get_compounds_for_formulation(form_uid)


@router.get(
    "/compounds/{comp_uid}/similar", response_model=List[CompoundStats]
)
def similar_compounds(
    comp_uid: str, limit: int = Query(5, ge=1, le=50)
) -> List[Dict[str, Any]]:
    _ensure()
    return neo4j_kg.find_similar_compounds(comp_uid, limit=limit)

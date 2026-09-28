"""Smart Collections API (W6-3 / P2-3).

Thin HTTP adapter over ``services.smart_collections``. Auth is enforced by the
global bearer-token middleware, same as the other routers.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..config import get_settings
from ..services import smart_collections as sc

router = APIRouter(prefix="/api/collections", tags=["collections"])


class FiltersIn(BaseModel):
    date_from: str | int | None = None
    date_to: str | int | None = None
    domain_allowlist: list[str] | None = None


class ScheduleIn(BaseModel):
    enabled: bool = True
    interval_hours: float = 24.0


class CollectionCreate(BaseModel):
    project_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1)
    filters: FiltersIn | None = None
    screening_preset: str | None = None
    schedule: ScheduleIn | None = None


class CollectionUpdate(BaseModel):
    name: str | None = None
    query: str | None = None
    filters: FiltersIn | None = None
    screening_preset: str | None = None
    schedule: ScheduleIn | None = None


def _get_or_404(project_id: str, collection_id: str) -> dict:
    col = sc.get_collection(project_id, collection_id)
    if col is None:
        raise HTTPException(status_code=404, detail="collection not found")
    return col


@router.get("")
def list_collections(project_id: str = Query(..., min_length=1)):
    return {"project_id": project_id, "collections": sc.list_collections(project_id)}


@router.post("", status_code=201)
def create_collection(body: CollectionCreate):
    try:
        col = sc.create_collection(
            body.project_id,
            name=body.name,
            query=body.query,
            filters=body.filters.model_dump() if body.filters else None,
            screening_preset=body.screening_preset,
            schedule=body.schedule.model_dump() if body.schedule else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return col


@router.get("/{collection_id}")
def get_collection_detail(
    collection_id: str, project_id: str = Query(..., min_length=1)
):
    return _get_or_404(project_id, collection_id)


@router.patch("/{collection_id}")
def update_collection(
    collection_id: str,
    body: CollectionUpdate,
    project_id: str = Query(..., min_length=1),
):
    try:
        col = sc.update_collection(
            project_id,
            collection_id,
            name=body.name,
            query=body.query,
            filters=body.filters.model_dump() if body.filters else None,
            screening_preset=body.screening_preset,
            schedule=body.schedule.model_dump() if body.schedule else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if col is None:
        raise HTTPException(status_code=404, detail="collection not found")
    return col


@router.delete("/{collection_id}")
def delete_collection(
    collection_id: str, project_id: str = Query(..., min_length=1)
):
    ok = sc.delete_collection(project_id, collection_id)
    if not ok:
        raise HTTPException(status_code=404, detail="collection not found")
    return {"deleted": True, "collection_id": collection_id}


@router.post("/{collection_id}/refresh")
def refresh_collection(
    collection_id: str, project_id: str = Query(..., min_length=1)
):
    try:
        snap = sc.refresh_collection(
            project_id, collection_id, settings=get_settings(), actor="user"
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="collection not found")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return snap

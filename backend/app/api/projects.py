"""Project workspace CRUD — NotebookLM-style persistent sessions."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db.project_store import get_project_store
from ..domain.project_workspace import (
    MigrateLocalRequest,
    ProjectCreateRequest,
    ProjectDetail,
    ProjectSummary,
    ProjectUpdateRequest,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=list[ProjectSummary])
def list_projects() -> list[ProjectSummary]:
    return get_project_store().list_summaries()


@router.post("", response_model=ProjectDetail)
def create_project(req: ProjectCreateRequest) -> ProjectDetail:
    return get_project_store().create(title=req.title, requirement=req.requirement)


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(project_id: str) -> ProjectDetail:
    detail = get_project_store().get(project_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return detail


@router.put("/{project_id}", response_model=ProjectDetail)
def update_project(project_id: str, req: ProjectUpdateRequest) -> ProjectDetail:
    detail = get_project_store().update(project_id, req.workspace, title=req.title)
    if detail is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return detail


@router.delete("/{project_id}")
def delete_project(
    project_id: str,
    knowledge: str = Query(default="delete", pattern="^(delete|global)$"),
) -> dict:
    """Delete a project. ``knowledge=global`` keeps its KB as global; ``delete`` removes all."""
    result = get_project_store().delete(project_id, knowledge=knowledge)
    if result is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"ok": True, **result}


@router.get("/{project_id}/history")
def project_payload_history(project_id: str, limit: int = Query(default=20, ge=1, le=100)) -> dict:
    """Payload 版本审计(2026-09-05): 每次 update 前快照, 支持回滚。"""
    detail = get_project_store().get(project_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Project not found")
    versions = get_project_store().list_payload_history(project_id, limit=limit)
    return {"project_id": project_id, "versions": versions}


@router.post("/{project_id}/rollback/{version}", response_model=ProjectDetail)
def rollback_project(project_id: str, version: int) -> ProjectDetail:
    """回滚 payload 到指定历史版本(当前值先快照, 再恢复目标)。"""
    try:
        detail = get_project_store().rollback_payload(project_id, version)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"version {version} not found")
    if detail is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return detail


@router.get("/{project_id}/db-stats")
def project_db_stats(project_id: str) -> dict:
    """Real database counts for a project (docs / campaigns / experiments)."""
    return {"project_id": project_id, **get_project_store().db_stats(project_id)}


@router.post("/migrate-local", response_model=list[ProjectSummary])
def migrate_local(req: MigrateLocalRequest) -> list[ProjectSummary]:
    if not req.snapshots:
        return []
    return get_project_store().migrate_legacy(req.snapshots)

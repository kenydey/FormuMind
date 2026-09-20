"""Project workspace CRUD — NotebookLM-style persistent sessions."""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..db.project_store import get_project_store
from ..domain.project_workspace import (
    MigrateLocalRequest,
    ProjectCreateRequest,
    ProjectDetail,
    ProjectSummary,
    ProjectUpdateRequest,
)
from ..services import project_exports as exports_svc

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ExportFileOut(BaseModel):
    name: str
    size: int
    updated_at: str
    content_type: str


class ExportTextBody(BaseModel):
    filename: str = Field(..., min_length=1, max_length=128)
    content: str = Field(..., max_length=5_000_000)
    content_type: str | None = None


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
    # Best-effort: promote requirement.materials into the global catalog.
    try:
        from ..services.material_promote import safe_propose_from_requirement

        ws = req.workspace if isinstance(req.workspace, dict) else {}
        req_blob = ws.get("requirement")
        if req_blob is None and detail.workspace and detail.workspace.requirement:
            req_blob = detail.workspace.requirement
        if req_blob is not None:
            safe_propose_from_requirement(req_blob, source_ref=f"project:{project_id}")
    except Exception:
        pass
    # P4.2: optional dossier auto-patch (default OFF via wiki_dossier_auto_patch).
    try:
        from ..services.wiki.dossier import notify_dossier_event

        notify_dossier_event(project_id, "project_updated")
    except Exception:
        pass
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
    try:
        n = exports_svc.purge_project_exports(project_id)
        result = {**result, "exports_removed": n}
    except Exception:
        pass
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


# ── Dim-4: project export file shelf ─────────────────────────────────────────


@router.get("/{project_id}/exports", response_model=list[ExportFileOut])
def list_project_exports(project_id: str) -> list[ExportFileOut]:
    return [
        ExportFileOut(
            name=f.name,
            size=f.size,
            updated_at=f.updated_at,
            content_type=f.content_type,
        )
        for f in exports_svc.list_exports(project_id)
    ]


@router.post("/{project_id}/exports", response_model=ExportFileOut)
def save_project_export(project_id: str, body: ExportTextBody) -> ExportFileOut:
    """Save UTF-8 text (CSV / JSON / Markdown) into the project export shelf."""
    info = exports_svc.save_export_bytes(project_id, body.filename, body.content.encode("utf-8"))
    return ExportFileOut(
        name=info.name,
        size=info.size,
        updated_at=info.updated_at,
        content_type=info.content_type,
    )


@router.post("/{project_id}/exports/upload", response_model=ExportFileOut)
async def upload_project_export(
    project_id: str,
    file: UploadFile = File(...),
    filename: str | None = Form(default=None),
) -> ExportFileOut:
    """Multipart binary upload (PDF / XLSX) into the project export shelf."""
    raw = await file.read()
    name = filename or file.filename or "export.bin"
    info = exports_svc.save_export_bytes(project_id, name, raw)
    return ExportFileOut(
        name=info.name,
        size=info.size,
        updated_at=info.updated_at,
        content_type=info.content_type,
    )


@router.get("/{project_id}/exports/{filename}")
def download_project_export(project_id: str, filename: str) -> FileResponse:
    path = exports_svc.resolve_export_path(project_id, filename)
    return FileResponse(
        path,
        filename=path.name,
        media_type=exports_svc.guess_content_type(path.name),
    )


@router.delete("/{project_id}/exports/{filename}")
def delete_project_export(project_id: str, filename: str) -> dict:
    exports_svc.delete_export(project_id, filename)
    return {"ok": True, "filename": filename}

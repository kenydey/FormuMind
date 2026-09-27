"""Artifact version API (W4-1 / P1-16).

Immutable version lineage for formulation / report / knowledge artifacts:
lineages group versions; versions move staging → pending → finalized.

Auth is enforced by the global bearer-token middleware (see middleware/api_auth),
same as the other routers; no per-endpoint auth code here.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import get_settings
from ..services import artifact_diff as diffsvc
from ..services import artifact_versions as svc

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


class CreateLineageBody(BaseModel):
    project_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=256)
    kind: str = Field(default="report", max_length=64)


class CreateVersionBody(BaseModel):
    content: str = Field(default="", max_length=2_000_000)
    based_on_version_id: str | None = Field(default=None, max_length=64)
    actor: str | None = Field(default=None, max_length=128)


class SetContentBody(BaseModel):
    content: str = Field(..., max_length=2_000_000)


class RestoreVersionBody(BaseModel):
    actor: str | None = Field(default=None, max_length=128)


def _require_enabled() -> None:
    if not svc.artifact_versions_enabled(get_settings()):
        raise HTTPException(status_code=404, detail="artifact versions disabled")


@router.post("/lineages", response_model=dict)
def create_lineage(body: CreateLineageBody) -> dict:
    _require_enabled()
    try:
        lineage = svc.create_lineage(body.project_id, body.name, kind=body.kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return lineage.to_dict()


@router.post("/lineages/{lineage_id}/versions", response_model=dict)
def create_version(lineage_id: str, body: CreateVersionBody) -> dict:
    _require_enabled()
    try:
        version = svc.create_version(
            lineage_id,
            body.content.encode("utf-8"),
            actor=body.actor,
            based_on_version_id=body.based_on_version_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return version.to_dict()


@router.get("/lineages/{lineage_id}/versions", response_model=dict)
def list_versions(lineage_id: str) -> dict:
    """Versions of a lineage plus the basedOnVersionId derivation graph."""
    _require_enabled()
    try:
        return svc.list_versions(lineage_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/versions/{version_id}", response_model=dict)
def get_version(version_id: str) -> dict:
    _require_enabled()
    try:
        version = svc.get_version(version_id)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if version is None:
        raise HTTPException(status_code=404, detail="version not found")
    return version.to_dict()


@router.get("/versions/{version_id}/diff", response_model=dict)
def diff_version(version_id: str, against: str) -> dict:
    """Diff two version snapshots: ``version_id`` (new) against ``against`` (old).

    Contract (consumed by the frontend version panel):
    ``{"version_id", "against_id", "truncated", "ops": [{type, old_text, new_text}]}``.
    Computed synchronously; large inputs degrade to line-level ops with
    ``truncated=True`` (see ``services/artifact_diff``).
    """
    _require_enabled()
    if not against or len(against) > 64:
        raise HTTPException(status_code=400, detail="against must be a non-empty version id")
    try:
        old_bytes = svc.get_version_content(against)
        new_bytes = svc.get_version_content(version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    result = diffsvc.diff_versions(
        old_bytes.decode("utf-8", errors="replace"),
        new_bytes.decode("utf-8", errors="replace"),
    )
    return {
        "version_id": version_id,
        "against_id": against,
        "truncated": result["truncated"],
        "ops": result["ops"],
    }


@router.get("/versions/{version_id}/verify", response_model=dict)
def verify_version(version_id: str) -> dict:
    """Recompute content sha256; detects post-hoc tampering of the snapshot."""
    _require_enabled()
    try:
        return svc.verify_version(version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.put("/versions/{version_id}/content", response_model=dict)
def set_version_content(version_id: str, body: SetContentBody) -> dict:
    """Rewrite content — staging only; finalized versions are immutable."""
    _require_enabled()
    try:
        version = svc.set_version_content(version_id, body.content.encode("utf-8"))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return version.to_dict()


@router.post("/versions/{version_id}/restore", response_model=dict)
def restore_version(version_id: str, body: RestoreVersionBody) -> dict:
    """W4-4 / P1-37: restore = create a new staging version from an old one.

    Copy-on-write: ``create_version`` with ``content=None`` copies the old
    version's content snapshot; ``based_on_version_id`` points at the old
    version so the derivation graph shows the rollback branch. History is
    never mutated — the old (possibly finalized) version is untouched.
    """
    _require_enabled()
    try:
        old = svc.get_version(version_id)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if old is None:
        raise HTTPException(status_code=404, detail="version not found")
    try:
        new = svc.create_version(
            old.lineage_id,
            None,  # copy-on-write from the based-on version
            actor=body.actor,
            based_on_version_id=version_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return new.to_dict()


@router.post("/versions/{version_id}/submit", response_model=dict)
def submit_version(version_id: str) -> dict:
    """staging → pending."""
    _require_enabled()
    try:
        version = svc.submit_version(version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return version.to_dict()


@router.post("/versions/{version_id}/finalize", response_model=dict)
def finalize_version(version_id: str) -> dict:
    """pending → finalized (immutable; triggers W4-2 evidence freeze fail-open)."""
    _require_enabled()
    try:
        version = svc.finalize_version(version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return version.to_dict()


@router.get("/versions/{version_id}/evidence", response_model=dict)
def get_evidence(version_id: str) -> dict:
    """Frozen evidence snapshot (manifest/evidence/execution) — read-only."""
    _require_enabled()
    try:
        return svc.get_evidence(version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/versions/{version_id}/evidence/verify", response_model=dict)
def verify_evidence(version_id: str) -> dict:
    """Recompute the evidence checksum; reports integrity + live-manifest drift."""
    _require_enabled()
    try:
        return svc.verify_evidence(version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

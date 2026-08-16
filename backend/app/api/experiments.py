"""Experiment feedback & model-training endpoints (DOE result回灌).

Lab/DOE results are submitted here, persisted, and used to (re)train the
per-(domain, metric) prediction models that supersede the empirical surrogate.

The workbench routes persist per-campaign execution rows for AG Grid editing
and BayBE closed-loop feedback from ``actual_params`` / ``measurements``.
Workbench row data is stored in Datalab (SSOT) via :class:`DatalabCampaignStore`.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from ..config import get_settings
from ..db.campaign_store import get_campaign_store
from ..db.campaign_types import WorkbenchRow
from ..db.models import Campaign
from ..domain.schemas import DOEPlan, ExperimentSubmission, ModelInfo, ProductDomain, Requirement, TrainingReport
from ..services import io_export
from ..services.training import registry

router = APIRouter(prefix="/api", tags=["experiments"])


class GridRowUpdate(BaseModel):
    id: int
    status: str = "Pending"
    actual_params: dict[str, float] = Field(default_factory=dict)
    measurements: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None          # Phase 2.2
    tags: list[str] = Field(default_factory=list)  # Phase 2.4


class BatchUpdateRequest(BaseModel):
    campaign_id: int
    rows: list[GridRowUpdate]
    trigger_loop: bool | None = None
    requirement: Requirement | None = None
    optimize_engine: str | None = None
    doe_engine: str | None = None
    campaign_state: str | None = None


class WorkbenchRowResponse(BaseModel):
    id: int
    campaign_id: int
    item_id: str = ""
    status: str
    planned_params: dict[str, Any]
    actual_params: dict[str, float]
    measurements: dict[str, Any]
    # Phase 2
    note: str | None = None
    tags: list[str] = Field(default_factory=list)
    parent_sample_id: str | None = None
    parent_campaign_id: int | None = None


class WorkbenchCampaignResponse(BaseModel):
    campaign_id: int
    name: str
    strategy: str
    status: str
    project_id: str | None = None
    primary_metric: str | None = None
    objectives_snapshot: list[dict[str, Any]] = Field(default_factory=list)
    loop_history: list[dict[str, Any]] = Field(default_factory=list)
    rows: list[WorkbenchRowResponse]


class WorkbenchSyncResponse(BaseModel):
    updated: int
    rows: list[WorkbenchRowResponse]
    training_ingested: int = 0
    training_message: str = ""
    loop_task_id: str | None = None
    loop_message: str = ""


class CreateWorkbenchCampaignRequest(BaseModel):
    plan: DOEPlan
    name: str | None = None
    strategy: str = "BayBE-LHS"
    project_id: str | None = None
    requirement: Requirement | None = None


def _campaign_response(campaign: Campaign, rows: list[WorkbenchRow]) -> WorkbenchCampaignResponse:
    return WorkbenchCampaignResponse(
        campaign_id=campaign.id,
        name=campaign.name,
        strategy=campaign.strategy,
        status=campaign.status,
        project_id=campaign.project_id,
        primary_metric=campaign.primary_metric,
        objectives_snapshot=campaign.objectives_snapshot or [],
        loop_history=campaign.loop_history or [],
        rows=[_row_response(r) for r in rows],
    )


def _row_response(row: WorkbenchRow) -> WorkbenchRowResponse:
    return WorkbenchRowResponse(
        id=row.id,
        campaign_id=row.campaign_id,
        item_id=row.item_id,
        status=row.status,
        planned_params=row.planned_params or {},
        actual_params=row.actual_params or {},
        measurements=row.measurements or {},
        note=row.note,
        tags=row.tags or [],
        parent_sample_id=row.parent_sample_id,
        parent_campaign_id=row.parent_campaign_id,
    )


@router.post("/experiments", response_model=TrainingReport)
def submit_experiments(submission: ExperimentSubmission) -> TrainingReport:
    """Ingest measured DOE results and (optionally) retrain models."""
    registry.add(submission.records, retrain=submission.retrain)
    trained = registry.info()
    msg = (
        f"Ingested {len(submission.records)} record(s); "
        f"{len(trained)} model(s) active."
    )
    if not trained:
        msg += f" Need >= {get_settings().min_train_samples} samples per metric to train."
    return TrainingReport(trained=trained, total_records=registry.total_records, message=msg)


@router.post("/experiments/import-csv", response_model=TrainingReport)
async def import_experiments_csv(
    file: UploadFile = File(...),
    domain: ProductDomain | None = Query(None, description="Fallback domain when the CSV omits it"),
    retrain: bool = Query(True),
) -> TrainingReport:
    """Import a filled-in DOE/experiment CSV (the worksheet produced by
    ``GET /api/doe/{plan_id}/export``) and (optionally) retrain models."""
    try:
        raw = await file.read()
        if len(raw) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="文件过大，最大20MB")
        try:
            text = raw.decode("utf-8-sig")  # tolerate Excel's UTF-8 BOM
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        try:
            records = io_export.csv_to_records(text, default_domain=domain)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not records:
            raise HTTPException(status_code=422, detail="No rows with measured values found in the CSV.")

        await run_in_threadpool(registry.add, records, retrain=retrain)
        trained = registry.info()
        msg = (
            f"Imported {len(records)} record(s) from {file.filename or 'upload'}; "
            f"{len(trained)} model(s) active."
        )
        if not trained:
            msg += f" Need >= {get_settings().min_train_samples} samples per metric to train."
        return TrainingReport(trained=trained, total_records=registry.total_records, message=msg)
    finally:
        await file.close()


@router.post("/train", response_model=TrainingReport)
def train_models() -> TrainingReport:
    """Force a retrain over all stored experiments."""
    trained = registry.train()
    return TrainingReport(
        trained=trained,
        total_records=registry.total_records,
        message=f"Retrained {len(trained)} model(s) from {registry.total_records} records.",
    )


@router.get("/models", response_model=list[ModelInfo])
def list_models() -> list[ModelInfo]:
    return registry.info()


class ExperimentSummary(BaseModel):
    """One stored experiment, identified by row id.

    ``ExperimentRecord`` carries no id — it is the training-facing shape — so
    anything that needs to *reference* an experiment (attaching a QC report,
    reading back its typed measurements) has to go through this.
    """

    id: int
    domain: str
    label: str = ""
    source: str = ""
    project_id: str = ""
    measured: dict[str, float] = Field(default_factory=dict)
    measurement_count: int = 0
    created_at: str | None = None


@router.get("/experiments", response_model=list[ExperimentSummary])
def list_experiments(
    domain: ProductDomain | None = Query(default=None),
    project_id: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[ExperimentSummary]:
    """Stored experiments, newest first."""
    from sqlalchemy import func

    from ..db.database import default_session_factory
    from ..db.models import ExperimentRow, MeasurementRow

    with default_session_factory()() as session:
        query = session.query(ExperimentRow)
        if domain is not None:
            query = query.filter(ExperimentRow.domain == domain.value)
        if project_id:
            query = query.filter(ExperimentRow.project_id == project_id)
        rows = query.order_by(ExperimentRow.id.desc()).limit(limit).all()

        counts = dict(
            session.query(MeasurementRow.experiment_id, func.count(MeasurementRow.id))
            .filter(MeasurementRow.experiment_id.in_([r.id for r in rows] or [0]))
            .group_by(MeasurementRow.experiment_id)
            .all()
        )

    return [
        ExperimentSummary(
            id=row.id,
            domain=row.domain,
            label=row.label or "",
            source=row.source or "",
            project_id=row.project_id or "",
            measured=dict(row.measured or {}),
            measurement_count=int(counts.get(row.id, 0)),
            created_at=row.created_at.isoformat() if row.created_at else None,
        )
        for row in rows
    ]


@router.post("/experiments/workbench/campaigns", response_model=WorkbenchCampaignResponse)
async def create_workbench_campaign(
    payload: CreateWorkbenchCampaignRequest,
) -> WorkbenchCampaignResponse:
    """Seed a campaign + pending rows from a generated DOE plan (Datalab samples)."""
    # TODO: 添加 owner 校验 — 单 token 模式下暂无法实现，迁移到多用户后需校验
    # payload.project_id 归属当前调用者。
    store = get_campaign_store()
    campaign = await store.create_from_plan(
        payload.plan,
        name=payload.name,
        strategy=payload.strategy,
        req=payload.requirement,
        project_id=payload.project_id,
    )
    rows = await store.list_rows(campaign.id)
    return _campaign_response(campaign, rows)


@router.get("/experiments/workbench/{campaign_id}", response_model=WorkbenchCampaignResponse)
async def get_workbench_campaign(
    campaign_id: int,
) -> WorkbenchCampaignResponse:
    # TODO: 添加 owner 校验 — 校验 campaign_id 归属当前调用者。
    store = get_campaign_store()
    campaign = await store.get_campaign(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    rows = await store.list_rows(campaign_id)
    return _campaign_response(campaign, rows)


@router.put("/experiments/workbench/sync", response_model=WorkbenchSyncResponse)
async def sync_workbench(
    payload: BatchUpdateRequest,
) -> WorkbenchSyncResponse:
    """Batch-update workbench rows from AG Grid edits (forwarded to Datalab)."""
    # TODO: 添加 owner 校验 — 校验 payload.campaign_id 归属当前调用者。
    store = get_campaign_store()
    if await store.get_campaign(payload.campaign_id) is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    updated, rows = await store.batch_sync(
        payload.campaign_id,
        [row.model_dump() for row in payload.rows],
    )
    from ..services.workbench_training import ingest_workbench_rows

    train_result = await run_in_threadpool(ingest_workbench_rows, payload.campaign_id, rows)
    training_ingested = int(train_result.get("ingested") or 0)
    training_message = str(train_result.get("message") or "")

    from ..services.workbench_loop import dispatch_loop_after_sync

    loop_task_id, loop_message = dispatch_loop_after_sync(
        training_ingested=training_ingested,
        workbench_campaign_id=payload.campaign_id,
        requirement=payload.requirement,
        trigger_loop=payload.trigger_loop,
        optimize_engine=payload.optimize_engine or "auto",
        doe_engine=payload.doe_engine or "auto",
        campaign_state=payload.campaign_state,
    )

    return WorkbenchSyncResponse(
        updated=updated,
        rows=[_row_response(r) for r in rows],
        training_ingested=training_ingested,
        training_message=training_message,
        loop_task_id=loop_task_id,
        loop_message=loop_message,
    )


# ── Experiment attachments (Phase 0.2) ────────────────────────────────────────

class AttachmentResponse(BaseModel):
    id: str
    experiment_id: int
    source_document_id: str
    kind: str = "qc_report"
    filename: str = ""
    note: str = ""
    created_at: str | None = None


@router.get("/experiments/{experiment_id}/attachments",
            response_model=list[AttachmentResponse])
def get_experiment_attachments(
    experiment_id: int,
) -> list[AttachmentResponse]:
    """List attachments (QC reports, spectra, images) linked to an experiment."""
    from ..db.measurement_store import get_measurement_store

    store = get_measurement_store()
    attachments = store.attachments_for(experiment_id)
    return [
        AttachmentResponse(
            id=a.id,
            experiment_id=a.experiment_id,
            source_document_id=a.source_document_id,
            kind=a.kind,
            filename="",
            note=a.note or "",
            created_at=a.created_at.isoformat() if a.created_at else None,
        )
        for a in attachments
    ]


@router.post("/experiments/{experiment_id}/attachments",
             response_model=AttachmentResponse)
async def upload_experiment_attachment(
    experiment_id: int,
    file: UploadFile = File(...),
    kind: str = Query(default="qc_report"),
    note: str = Query(default=""),
) -> AttachmentResponse:
    """Upload a file attachment (QC report, microscope image, etc.)
    and link it to an experiment.

    The file is forwarded to Datalab ELN as the document store;
    a local ``ExperimentAttachment`` row keeps the reference.
    """
    from ..db.measurement_store import get_measurement_store
    from ..db.datalab_client import upload_file as datalab_upload_file

    settings = get_settings()
    filename = file.filename or "upload"

    # Upload to Datalab ELN (best-effort; falls back to local-only)
    content = await file.read()
    source_document_id = (
        await datalab_upload_file(settings.datalab_api_url, content, filename) or ""
    )

    # If Datalab upload failed, generate a local reference
    if not source_document_id:
        import uuid as _uuid

        source_document_id = f"local-{_uuid.uuid4().hex[:12]}"

    # Create local attachment link
    store = get_measurement_store()
    attachment_id = store.attach(
        experiment_id, source_document_id, kind=kind, note=note
    )
    if attachment_id is None:
        raise HTTPException(
            status_code=409,
            detail=f"Attachment already exists for experiment={experiment_id} "
            f"and document={source_document_id}",
        )

    return AttachmentResponse(
        id=attachment_id,
        experiment_id=experiment_id,
        source_document_id=source_document_id,
        kind=kind,
        filename=filename,
        note=note,
        created_at=None,
    )


# ── Phase 2.4: tag management ──────────────────────────────────────────────

class TagUpdateRequest(BaseModel):
    tags: list[str] = Field(default_factory=list)


@router.put("/experiments/workbench/{campaign_id}/rows/{row_id}/tags")
async def update_row_tags(
    campaign_id: int,
    row_id: int,
    body: TagUpdateRequest,
) -> WorkbenchRowResponse:
    """Set tags on a workbench row."""
    store = get_campaign_store()
    rows = await store.list_rows(campaign_id)
    match = next((r for r in rows if r.id == row_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="Row not found")
    match.tags = body.tags
    updated, refreshed = await store.batch_sync(
        campaign_id,
        [{"id": row_id, "tags": body.tags, "actual_params": match.actual_params,
          "measurements": match.measurements, "status": match.status, "note": match.note}],
    )
    fresh = next((r for r in refreshed if r.id == row_id), match)
    return _row_response(fresh)


# ── Phase 2.3: sample lineage ─────────────────────────────────────────────

@router.get("/experiments/workbench/{campaign_id}/rows/{row_id}/lineage")
async def get_row_lineage(
    campaign_id: int,
    row_id: int,
) -> list[WorkbenchRowResponse]:
    """Walk parent chain for a workbench row."""
    store = get_campaign_store()
    rows = await store.list_rows(campaign_id)
    match = next((r for r in rows if r.id == row_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="Row not found")

    lineage: list[WorkbenchRowResponse] = []
    current = match
    visited: set[str] = set()
    while current:
        key = f"{current.campaign_id}:{current.id}"
        if key in visited:
            break
        visited.add(key)
        lineage.append(_row_response(current))
        if current.parent_sample_id and current.parent_campaign_id:
            parent_rows = await store.list_rows(current.parent_campaign_id)
            parent = next(
                (r for r in parent_rows if r.item_id == current.parent_sample_id), None
            )
            current = parent
        else:
            break
    return lineage


# ── Phase 2.5: cross-campaign search ──────────────────────────────────────

class ExperimentSearchResult(BaseModel):
    row_id: int
    campaign_id: int
    campaign_name: str
    item_id: str
    status: str
    planned_params: dict[str, Any]
    measurements: dict[str, Any]


@router.get("/experiments/search", response_model=list[ExperimentSearchResult])
async def search_experiments(
    q: str = Query(default="", description="搜索关键词"),
) -> list[ExperimentSearchResult]:
    """Search across all campaigns by keyword (tags, params, measurements)."""
    # In Datalab mode: delegate to Datalab search API
    settings = get_settings()
    backend = (settings.campaign_backend or "sqlite").lower()

    if backend in ("datalab", "auto"):
        from ..db.datalab_client import check_datalab_reachable
        ok, _ = await run_in_threadpool(check_datalab_reachable, settings.datalab_api_url, timeout=2.0)
        if ok:
            import httpx
            try:
                async with httpx.AsyncClient(
                    base_url=settings.datalab_api_url.rstrip("/"),
                    timeout=10.0,
                ) as client:
                    resp = await client.get("/search/", params={"q": q})
                    if resp.status_code < 400:
                        return _parse_datalab_search(resp.json())
            except Exception:
                pass  # fall through to local scan

    # Local SQLite scan
    results: list[ExperimentSearchResult] = []
    from ..db.database import default_session_factory
    with default_session_factory()() as session:
        campaigns = session.query(Campaign).all()
        for camp in campaigns:
            refs = camp.sample_refs or []
            for ref in refs:
                if not q:
                    continue
                # Simple substring match across tags + note
                tags = " ".join(ref.get("tags") or [])
                note = str(ref.get("note") or "")
                text = f"{tags} {note}".lower()
                if q.lower() in text:
                    results.append(ExperimentSearchResult(
                        row_id=int(ref["id"]),
                        campaign_id=camp.id,
                        campaign_name=camp.name,
                        item_id=str(ref.get("item_id", "")),
                        status=str(ref.get("status", "Pending")),
                        planned_params=ref.get("planned_params", {}),
                        measurements=ref.get("measurements", {}),
                    ))
    return results


def _parse_datalab_search(body: list[dict]) -> list[ExperimentSearchResult]:
    results: list[ExperimentSearchResult] = []
    for sample in body:
        blocks = sample.get("blocks_obj", {})
        params_block = blocks.get("formumind_params", {})
        meas_block = blocks.get("formumind_measurements", {})
        results.append(ExperimentSearchResult(
            row_id=0,
            campaign_id=0,
            campaign_name="",
            item_id=sample.get("item_id", ""),
            status=str(sample.get("status", "Pending")),
            planned_params=params_block.get("data", {}),
            measurements=meas_block.get("data", {}),
        ))
    return results


# ── Phase 3.3: convergence webhook receiver ───────────────────────────────

class ConvergenceWebhookPayload(BaseModel):
    campaign_id: int
    converged: bool = True
    round_count: int = 0
    message: str = ""


@router.post("/experiments/hooks/convergence")
async def convergence_webhook(
    payload: ConvergenceWebhookPayload,
) -> dict[str, str]:
    """Receive convergence notification from Datalab or loop engine."""
    logger = __import__("logging").getLogger(__name__)
    logger.info(
        "Convergence webhook: campaign=%d converged=%s round=%d msg=%s",
        payload.campaign_id,
        payload.converged,
        payload.round_count,
        payload.message,
    )
    # Forward to WebSocket / SSE notification system
    # (handled by existing notification pipeline)
    return {"status": "received", "campaign_id": str(payload.campaign_id)}

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
    raw = await file.read()
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

    registry.add(records, retrain=retrain)
    trained = registry.info()
    msg = (
        f"Imported {len(records)} record(s) from {file.filename or 'upload'}; "
        f"{len(trained)} model(s) active."
    )
    if not trained:
        msg += f" Need >= {get_settings().min_train_samples} samples per metric to train."
    return TrainingReport(trained=trained, total_records=registry.total_records, message=msg)


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


@router.post("/experiments/workbench/campaigns", response_model=WorkbenchCampaignResponse)
async def create_workbench_campaign(
    payload: CreateWorkbenchCampaignRequest,
) -> WorkbenchCampaignResponse:
    """Seed a campaign + pending rows from a generated DOE plan (Datalab samples)."""
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
    store = get_campaign_store()
    if await store.get_campaign(payload.campaign_id) is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    updated, rows = await store.batch_sync(
        payload.campaign_id,
        [row.model_dump() for row in payload.rows],
    )
    from ..services.workbench_training import ingest_workbench_rows

    train_result = ingest_workbench_rows(payload.campaign_id, rows)
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

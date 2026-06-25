"""Experiment feedback & model-training endpoints (DOE result回灌).

Lab/DOE results are submitted here, persisted, and used to (re)train the
per-(domain, metric) prediction models that supersede the empirical surrogate.

The workbench routes persist per-campaign execution rows for AG Grid editing
and BayBE closed-loop feedback from ``actual_params`` / ``measurements``.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from ..config import get_settings
from ..db.campaign_store import get_campaign_store
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


class WorkbenchRowResponse(BaseModel):
    id: int
    campaign_id: int
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
    rows: list[WorkbenchRowResponse]


class WorkbenchSyncResponse(BaseModel):
    updated: int
    rows: list[WorkbenchRowResponse]


class CreateWorkbenchCampaignRequest(BaseModel):
    plan: DOEPlan
    name: str | None = None
    strategy: str = "BayBE-LHS"
    project_id: str | None = None
    requirement: Requirement | None = None


def _campaign_response(campaign, rows) -> WorkbenchCampaignResponse:
    return WorkbenchCampaignResponse(
        campaign_id=campaign.id,
        name=campaign.name,
        strategy=campaign.strategy,
        status=campaign.status,
        project_id=campaign.project_id,
        primary_metric=campaign.primary_metric,
        objectives_snapshot=campaign.objectives_snapshot or [],
        rows=[_row_response(r) for r in rows],
    )


def _row_response(row) -> WorkbenchRowResponse:
    return WorkbenchRowResponse(
        id=row.id,
        campaign_id=row.campaign_id,
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
def create_workbench_campaign(req: CreateWorkbenchCampaignRequest) -> WorkbenchCampaignResponse:
    """Seed a campaign + pending rows from a generated DOE plan."""
    store = get_campaign_store()
    campaign = store.create_from_plan(
        req.plan,
        name=req.name,
        strategy=req.strategy,
        req=req.requirement,
        project_id=req.project_id,
    )
    rows = store.list_rows(campaign.id)
    return _campaign_response(campaign, rows)


@router.get("/experiments/workbench/{campaign_id}", response_model=WorkbenchCampaignResponse)
def get_workbench_campaign(campaign_id: int) -> WorkbenchCampaignResponse:
    store = get_campaign_store()
    campaign = store.get_campaign(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    rows = store.list_rows(campaign_id)
    return _campaign_response(campaign, rows)


@router.put("/experiments/workbench/sync", response_model=WorkbenchSyncResponse)
def sync_workbench(req: BatchUpdateRequest) -> WorkbenchSyncResponse:
    """Batch-update workbench rows from AG Grid edits."""
    store = get_campaign_store()
    if store.get_campaign(req.campaign_id) is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    updated, rows = store.batch_sync(
        req.campaign_id,
        [row.model_dump() for row in req.rows],
    )
    return WorkbenchSyncResponse(updated=updated, rows=[_row_response(r) for r in rows])

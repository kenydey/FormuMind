"""Experiment feedback & model-training endpoints (DOE result回灌).

Lab/DOE results are submitted here, persisted, and used to (re)train the
per-(domain, metric) prediction models that supersede the empirical surrogate.
"""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from ..config import get_settings
from ..domain.schemas import ExperimentSubmission, ModelInfo, ProductDomain, TrainingReport
from ..services import io_export
from ..services.training import registry

router = APIRouter(prefix="/api", tags=["experiments"])


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

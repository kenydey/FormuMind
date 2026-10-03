"""Experiment feedback & model-training endpoints (DOE result回灌).

Lab/DOE results are submitted here, persisted, and used to (re)train the
per-(domain, metric) prediction models that supersede the empirical surrogate.

The workbench routes persist per-campaign execution rows for AG Grid editing
and BayBE closed-loop feedback from ``actual_params`` / ``measurements``.
Workbench row data is stored in Datalab (SSOT) via :class:`DatalabCampaignStore`.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import func, select, true

from ..config import get_settings
from ..db.campaign_store import get_campaign_store
from ..db.campaign_types import WorkbenchRow
from ..db.models import Campaign
from ..domain.schemas import DOEPlan, ExperimentSubmission, ModelInfo, ProductDomain, Requirement, TrainingReport
from ..services import io_export
from ..services.training import registry
from ._uploads import read_upload_capped

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["experiments"])


def _ensure_eln_when_required() -> None:
    """Fail fast when product ELN backends are configured but Datalab is down.

    Some list endpoints still read the local SQL index for ids/labels. Under
    product defaults (``datalab`` + ``DATALAB_REQUIRED``) that must not look
    like a healthy sqlite ledger — probe ELN first and raise the shared 503.
    """
    from ..db.datalab_client import DatalabUnavailableError, check_datalab_reachable

    settings = get_settings()
    backends = {
        (settings.campaign_backend or "").lower(),
        (settings.experiment_backend or "").lower(),
    }
    if not (settings.datalab_required or "datalab" in backends):
        return
    ok, reason = check_datalab_reachable(
        settings.datalab_api_url,
        timeout=min(2.0, settings.datalab_timeout_seconds),
    )
    if not ok:
        raise DatalabUnavailableError(settings.datalab_api_url, reason)


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
    refcode: str | None = None
    # P5: 该行测量已回灌为训练数据（experiments 有 wb 标签且 measured 非空）
    ingested: bool = False


class WorkbenchCampaignResponse(BaseModel):
    campaign_id: int
    name: str
    strategy: str
    status: str
    project_id: str | None = None
    primary_metric: str | None = None
    objectives_snapshot: list[dict[str, Any]] = Field(default_factory=list)
    loop_history: list[dict[str, Any]] = Field(default_factory=list)
    # Batch B: idle|running|converged|paused|failed (+ rounds / rmse / message)
    loop_status: dict[str, Any] | None = None
    rows: list[WorkbenchRowResponse]


class WorkbenchSyncResponse(BaseModel):
    updated: int
    rows: list[WorkbenchRowResponse]
    training_ingested: int = 0
    training_message: str = ""
    prediction_bias: dict | None = None
    kg_written: int | None = None
    # None = skipped/disabled; >=0 = written count; -1 = ingest raised (UI refresh + tip)
    kg_error: str | None = None
    loop_task_id: str | None = None
    loop_message: str = ""
    loop_status: dict[str, Any] | None = None
    quality: dict | None = None


class CreateWorkbenchCampaignRequest(BaseModel):
    plan: DOEPlan
    name: str | None = None
    strategy: str = "BayBE-LHS"
    project_id: str | None = None
    requirement: Requirement | None = None


def _ingested_item_ids(campaign_id: int, rows: list[WorkbenchRow]) -> set[str]:
    """P5: item_ids whose measurements were already ingested into training.

    One query: experiments rows with label ``wb:{cid}:{item_id}`` and non-empty
    measured JSON. Any lookup failure degrades to an empty set (badges off).
    """
    try:
        from ..db.database import default_session_factory
        from ..db.models import ExperimentRow

        item_ids = [r.item_id for r in rows if r.item_id]
        if not item_ids:
            return set()
        labels = [f"wb:{campaign_id}:{it}" for it in item_ids]
        with default_session_factory()() as session:
            candidates = (
                session.query(ExperimentRow.label, ExperimentRow.measured)
                .filter(ExperimentRow.label.in_(labels))
                .all()
            )
        ingested: set[str] = set()
        for label, measured in candidates:
            item_id = str(label).split(":", 2)[-1] if label else ""
            if item_id and measured:
                ingested.add(item_id)
        return ingested
    except Exception:
        logger.warning(
            "ingested aggregation failed for campaign %s", campaign_id, exc_info=True
        )
        return set()


def _campaign_response(campaign: Campaign, rows: list[WorkbenchRow]) -> WorkbenchCampaignResponse:
    ingested_items = _ingested_item_ids(campaign.id, rows)
    loop_status = None
    try:
        from ..services.workbench_loop import campaign_loop_status

        loop_status = campaign_loop_status(int(campaign.id))
    except Exception:
        loop_status = None
    return WorkbenchCampaignResponse(
        campaign_id=campaign.id,
        name=campaign.name,
        strategy=campaign.strategy,
        status=campaign.status,
        project_id=campaign.project_id,
        primary_metric=campaign.primary_metric,
        objectives_snapshot=campaign.objectives_snapshot or [],
        loop_history=campaign.loop_history or [],
        loop_status=loop_status,
        rows=[_row_response(r, ingested_items) for r in rows],
    )


def _row_response(row: WorkbenchRow, ingested_items: set[str] | None = None) -> WorkbenchRowResponse:
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
        refcode=row.refcode,
        ingested=bool(row.item_id and ingested_items and row.item_id in ingested_items),
    )


def _retrain_note(requested: bool, retrained: bool | None) -> str:
    """Say so when ``auto_retrain=false`` vetoed a requested retrain."""
    if requested and retrained is False:
        return " Auto-retrain is off (FORMUMIND_AUTO_RETRAIN=false): call POST /api/train to refresh models."
    return ""


def _pinned_note(models: list[ModelInfo]) -> str:
    """Say so when a rolled-back (pinned) model kept serving instead of the retrained one."""
    pinned = [m for m in models if m.pinned and m.newer_version_id]
    if not pinned:
        return ""
    names = ", ".join(sorted({m.metric for m in pinned}))
    return (
        f" {len(pinned)} model(s) stay pinned to a rolled-back version ({names}); "
        "the retrained versions are archived — release the pin (POST /api/models/unpin) to use them."
    )


@router.post("/experiments", response_model=TrainingReport)
def submit_experiments(submission: ExperimentSubmission) -> TrainingReport:
    """Ingest measured DOE results and (optionally) retrain models."""
    retrained = registry.add(submission.records, retrain=submission.retrain)
    trained = registry.info()
    msg = (
        f"Ingested {len(submission.records)} record(s); "
        f"{len(trained)} model(s) active."
    )
    msg += _retrain_note(submission.retrain, retrained) + _pinned_note(trained)
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
        raw = await read_upload_capped(file, file.filename or "upload")
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

        retrained = await run_in_threadpool(registry.add, records, retrain=retrain)
        trained = registry.info()
        msg = (
            f"Imported {len(records)} record(s) from {file.filename or 'upload'}; "
            f"{len(trained)} model(s) active."
        )
        msg += _retrain_note(retrain, retrained) + _pinned_note(trained)
        if not trained:
            msg += f" Need >= {get_settings().min_train_samples} samples per metric to train."
        return TrainingReport(trained=trained, total_records=registry.total_records, message=msg)
    finally:
        await file.close()


@router.post("/train", response_model=TrainingReport, include_in_schema=False)
def train_models() -> TrainingReport:
    """Force a retrain over all stored experiments."""
    trained = registry.train()
    return TrainingReport(
        trained=trained,
        total_records=registry.total_records,
        message=(
            f"Retrained {len(trained)} model(s) from {registry.total_records} records."
            + _pinned_note(trained)
        ),
    )


@router.get("/models", response_model=list[ModelInfo])
def list_models() -> list[ModelInfo]:
    return registry.info()


@router.get("/models/versions")
def list_model_versions(
    project_id: str = Query(..., min_length=1),
    metric: str = Query(..., min_length=1),
) -> list[dict]:
    """P1 #20: list disk-backed surrogate versions (newest first)."""
    return registry.list_model_versions(project_id, metric)


class ModelRollbackBody(BaseModel):
    project_id: str = Field(..., min_length=1)
    metric: str = Field(..., min_length=1)
    version_id: str = Field(..., min_length=1)


@router.post("/models/rollback", response_model=ModelInfo)
def rollback_model(body: ModelRollbackBody) -> ModelInfo:
    """P1 #20: serve a prior artifact and pin it (retrains archive new versions beside it)."""
    info = registry.rollback_model(body.project_id, body.metric, body.version_id)
    if info is None:
        raise HTTPException(status_code=404, detail="model version not found")
    return info


class ModelUnpinBody(BaseModel):
    project_id: str = Field(..., min_length=1)
    metric: str = Field(..., min_length=1)


@router.post("/models/unpin", response_model=ModelInfo)
def unpin_model(body: ModelUnpinBody) -> ModelInfo:
    """Release a rollback pin and serve the newest archived version."""
    info = registry.unpin_model(body.project_id, body.metric)
    if info is None:
        raise HTTPException(status_code=404, detail="no archived model version for this metric")
    return info


class TrainingStatus(BaseModel):
    """B: 训练数据就绪度总览 — 让「寻优是预测器回声」透明化。"""

    total_records: int
    min_samples: int
    sufficient: bool  # total_records >= min_samples → GP/Ridge 才有真数据
    models_trained: int
    by_domain: dict[str, int] = Field(default_factory=dict)
    message: str = ""


@router.get("/training-status", response_model=TrainingStatus)
def training_status() -> TrainingStatus:
    """B: 训练数据可见性。数据不足时前端/用户应知寻优结果基于先验。"""
    from ..config import get_settings

    settings = get_settings()
    total = registry.total_records
    by_domain: dict[str, int] = {}
    for rec in registry.all_records():
        dom = rec.domain.value if hasattr(rec.domain, "value") else str(rec.domain)
        by_domain[dom] = by_domain.get(dom, 0) + 1
    sufficient = total >= settings.min_train_samples
    msg = (
        f"训练数据 {total} 条{'≥' if sufficient else '<'} min_samples {settings.min_train_samples}，"
        + ("GP/经验模型已可训练" if sufficient else "寻优结果为预测器先验（数据到位后收敛才是真的）")
    )
    return TrainingStatus(
        total_records=total,
        min_samples=settings.min_train_samples,
        sufficient=sufficient,
        models_trained=len(registry.info()),
        by_domain=by_domain,
        message=msg,
    )


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
    _ensure_eln_when_required()
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


class WorkbenchCampaignSummary(BaseModel):
    id: int
    name: str
    status: str
    strategy: str = ""
    row_count: int = 0
    project_id: str | None = None
    datalab_collection_id: str | None = None  # P1: DataLab 项目集合


@router.get("/experiments/workbench/campaigns", response_model=list[WorkbenchCampaignSummary])
def list_workbench_campaigns() -> list[WorkbenchCampaignSummary]:
    """All workbench campaigns, newest first, with row counts."""
    _ensure_eln_when_required()
    from ..db.database import default_session_factory
    from ..db.models import Campaign

    with default_session_factory()() as session:
        rows = session.query(Campaign).order_by(Campaign.id.desc()).all()
        return [
            WorkbenchCampaignSummary(
                id=c.id,
                name=c.name,
                status=c.status,
                strategy=c.strategy or "",
                row_count=len(c.sample_refs or []),
                project_id=c.project_id,
                datalab_collection_id=c.datalab_collection_id,
            )
            for c in rows
        ]


@router.post("/experiments/workbench/campaigns", response_model=WorkbenchCampaignResponse)
async def create_workbench_campaign(
    payload: CreateWorkbenchCampaignRequest,
    request: Request,
) -> WorkbenchCampaignResponse:
    """Seed a campaign + pending rows from a generated DOE plan (Datalab samples)."""
    from ..middleware.api_auth import assert_owner, get_current_owner

    current_owner = get_current_owner(request)
    # Phase 1 软校验：单 token 恒过，仅记录
    assert_owner(None, current_owner)
    store = get_campaign_store()
    campaign = await store.create_from_plan(
        payload.plan,
        name=payload.name,
        strategy=payload.strategy,
        req=payload.requirement,
        project_id=payload.project_id,
        owner_id=current_owner if current_owner != "default" else None,
    )
    if payload.project_id:
        try:
            from ..services.workbench_training import _link_campaign_to_project

            _link_campaign_to_project(str(payload.project_id), int(campaign.id))
        except Exception:
            pass
    rows = await store.list_rows(campaign.id)
    return _campaign_response(campaign, rows)


@router.get("/experiments/workbench/{campaign_id}", response_model=WorkbenchCampaignResponse)
async def get_workbench_campaign(
    campaign_id: int,
    request: Request,
) -> WorkbenchCampaignResponse:
    from ..middleware.api_auth import assert_owner, get_current_owner

    store = get_campaign_store()
    campaign = await store.get_campaign(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    assert_owner(getattr(campaign, "owner_id", None), get_current_owner(request))
    rows = await store.list_rows(campaign_id)
    return _campaign_response(campaign, rows)


@router.get("/experiments/workbench/{campaign_id}/bias-trend")
async def get_bias_trend(
    campaign_id: int,
    request: Request,
    threshold_rmse: float = Query(default=50.0, description="RMSE 告警阈值"),
) -> dict:
    """返回该 campaign 的 prediction_bias 趋势（loop_history 抽取）与阈值告警。"""
    from ..middleware.api_auth import assert_owner, get_current_owner

    store = get_campaign_store()
    campaign = await store.get_campaign(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    assert_owner(getattr(campaign, "owner_id", None), get_current_owner(request))
    history = campaign.loop_history or []
    trend = []
    alerts: list[str] = []
    for entry in history:
        if entry.get("type") != "prediction_bias":
            continue
        at = entry.get("at")
        bias = entry.get("bias") or entry.get("data") or {}
        # 兼容两种结构：{by_metric: {...}} 或直接 bias dict
        by_metric = bias.get("by_metric") if isinstance(bias, dict) else None
        if not by_metric and isinstance(entry.get("by_metric"), dict):
            by_metric = entry.get("by_metric")
        if not by_metric:
            continue
        n_rows = bias.get("n_rows") or entry.get("n_rows") or 0
        trend.append({"at": at, "n_rows": n_rows, "by_metric": by_metric})
        for metric, vals in by_metric.items():
            rmse = vals.get("rmse")
            if rmse is not None and rmse > threshold_rmse:
                alerts.append(f"{metric} RMSE {rmse:.1f} 超阈值 {threshold_rmse}（{at or 'unknown'}）")
    trend.sort(key=lambda x: x.get("at") or "")
    return {"campaign_id": campaign_id, "trend": trend, "alerts": alerts, "threshold_rmse": threshold_rmse}


@router.put("/experiments/workbench/sync", response_model=WorkbenchSyncResponse)
async def sync_workbench(
    payload: BatchUpdateRequest,
    request: Request,
) -> WorkbenchSyncResponse:
    """Batch-update workbench rows from AG Grid edits (forwarded to Datalab)."""
    from ..middleware.api_auth import assert_owner, get_current_owner

    store = get_campaign_store()
    _campaign = await store.get_campaign(payload.campaign_id)
    if _campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    assert_owner(getattr(_campaign, "owner_id", None), get_current_owner(request))

    updated, rows = await store.batch_sync(
        payload.campaign_id,
        [row.model_dump() for row in payload.rows],
    )
    # Best-effort: unknown factor keys → material pending queue / catalog.
    try:
        from ..services.material_promote import safe_propose_from_workbench_rows

        safe_propose_from_workbench_rows(payload.rows, campaign_id=payload.campaign_id)
    except Exception as exc:  # pragma: no cover
        logger.debug("workbench material promote skipped: %s", exc)

    from ..services.workbench_training import ingest_workbench_rows

    train_result = await run_in_threadpool(ingest_workbench_rows, payload.campaign_id, rows)
    training_ingested = int(train_result.get("ingested") or 0)
    training_message = str(train_result.get("message") or "")

    from ..services.workbench_loop import dispatch_loop_after_sync

    # P0 KG self-evolution: push measured results back into the KG (best-effort,
    # never blocks the sync response).
    kg_written: int | None = None
    kg_error: str | None = None
    try:
        from ..services import kg_feedback

        kg_written = kg_feedback.ingest_measured_evidence(payload.campaign_id)
    except Exception as exc:  # pragma: no cover - defense in depth
        logger.warning("kg_feedback ingest failed (non-fatal): %s", exc)
        kg_written = -1
        kg_error = str(exc)[:240]

    loop_task_id, loop_message = dispatch_loop_after_sync(
        training_ingested=training_ingested,
        workbench_campaign_id=payload.campaign_id,
        requirement=payload.requirement,
        trigger_loop=payload.trigger_loop,
        optimize_engine=payload.optimize_engine or "auto",
        doe_engine=payload.doe_engine or "auto",
        campaign_state=payload.campaign_state,
        project_id=getattr(_campaign, "project_id", None),
    )

    loop_status = None
    try:
        from ..services.workbench_loop import campaign_loop_status

        loop_status = campaign_loop_status(int(payload.campaign_id))
        if loop_task_id:
            loop_status = {**loop_status, "status": "running", "message": loop_message or loop_status.get("message")}
    except Exception:
        loop_status = None

    # P4.2: optional dossier S4/S5 patch after lab sync (default OFF).
    try:
        from ..services.wiki.dossier import notify_dossier_event_for_campaign

        notify_dossier_event_for_campaign(payload.campaign_id, "lab_recorded")
    except Exception as exc:
        logger.warning("dossier notify after sync failed (non-fatal): %s", exc)

    quality = train_result.get("quality")
    if quality is not None and not isinstance(quality, dict):
        quality = None

    return WorkbenchSyncResponse(
        updated=updated,
        rows=[
            _row_response(r, _ingested_item_ids(payload.campaign_id, rows))
            for r in rows
        ],
        training_ingested=training_ingested,
        training_message=training_message,
        prediction_bias=train_result.get("prediction_bias"),
        kg_written=kg_written,
        kg_error=kg_error,
        loop_task_id=loop_task_id,
        loop_message=loop_message,
        loop_status=loop_status,
        quality=quality,
    )


@router.post("/experiments/workbench/{campaign_id}/reconcile", response_model=dict)
async def reconcile_workbench(campaign_id: int, request: Request) -> dict:
    """Detect and prune stale ``sample_refs`` whose Datalab item was deleted.

    Only a definitive 404 prunes; network / 5xx failures are reported in
    ``errors`` and left untouched. Idempotent: a second call returns an empty
    ``removed`` list once the campaign is clean.
    """
    from ..middleware.api_auth import assert_owner, get_current_owner

    store = get_campaign_store()
    campaign = await store.get_campaign(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    assert_owner(getattr(campaign, "owner_id", None), get_current_owner(request))
    return await store.reconcile_sample_refs(campaign_id)


@router.get("/experiments/workbench/{campaign_id}/quality", response_model=dict)
async def workbench_quality(campaign_id: int) -> dict:
    """Data-quality snapshot for a campaign (read-only, no pruning).

    Returns ``{stale_count, stale_refs, errors_count, dropped_total}``:
    ``stale_*`` are sample refs whose Datalab item is gone (404), and
    ``dropped_total`` is the cumulative count of non-numeric / empty values
    discarded during workbench ingest (from ``data_quality`` loop_history
    events).
    """
    store = get_campaign_store()
    campaign = await store.get_campaign(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    probe = await store.probe_sample_refs(campaign_id)
    dropped_total = 0
    for entry in campaign.loop_history or []:
        if entry.get("type") == "data_quality":
            dropped_total += int(entry.get("dropped_values", 0))
    return {
        "stale_count": len(probe.get("stale", [])),
        "stale_refs": probe.get("stale", []),
        "errors_count": len(probe.get("errors", [])),
        "dropped_total": dropped_total,
    }


# ── Experiment attachments (Phase 0.2) ────────────────────────────────────────

async def _datalab_item_id_for(
    experiment_id: int = 0, campaign_id: int = 0, row_id: int = 0
) -> str | None:
    """Resolve the Datalab sample id a file upload should attach to.

    Workbench rows always live on a Datalab sample (formumind_cXX_rY_hash);
    plain experiments only after they have been ingested from such a row.
    Best-effort: any lookup failure yields ``None`` so uploads fall back to
    the local-attachment path instead of crashing.
    """
    try:
        if campaign_id > 0 and row_id > 0:
            from ..db.campaign_store import get_campaign_store

            rows = get_campaign_store().list_rows_sync(campaign_id)
            match = next((r for r in rows if r.id == row_id), None)
            return (match.item_id if match and match.item_id else None)
        if experiment_id > 0:
            from ..db.database import default_session_factory
            from ..db.models import ExperimentRow

            with default_session_factory() as session:
                row = session.get(ExperimentRow, experiment_id)
                return row.item_id if row else None
    except Exception:
        logger.warning("datalab item_id resolve failed", exc_info=True)
    return None


async def _upload_or_store_locally(
    content: bytes, filename: str, item_id: str | None = None
) -> str:
    """上传 Datalab；失败时落盘本地并返回指向真实文件的引用。

    旧实现失败时伪造 ``local-{uuid}`` 引用——文件字节被丢弃却返回 200，
    用户以为附件存在但内容永久丢失。现在失败路径把内容写入
    ``backend/data/attachments/``，引用可追溯到真实文件。
    """
    from ..db.datalab_client import upload_file as datalab_upload_file

    doc_id = await datalab_upload_file(
        get_settings().datalab_api_url, content, filename, item_id=item_id
    )
    if doc_id:
        return doc_id

    # 本地兜底：落盘 + 内容哈希引用（local-<sha1[:16]>）
    import hashlib
    import uuid as _uuid

    from ..config import get_settings as _gs

    digest = hashlib.sha1(content).hexdigest()[:16]
    local_id = f"local-{digest}"
    base = Path(_gs().db_url.replace("sqlite:///", "")).parent / "attachments"
    dest = base / f"{local_id}-{_uuid.uuid4().hex[:8]}-{filename}"
    try:
        base.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    except OSError:
        logger.exception("attachment local fallback write failed")
        raise HTTPException(
            status_code=503,
            detail="附件存储不可用（Datalab 上传失败且本地落盘失败），请稍后重试",
        )
    return local_id


class AttachmentResponse(BaseModel):
    id: str
    experiment_id: int
    source_document_id: str
    kind: str = "qc_report"
    filename: str = ""
    note: str = ""
    created_at: str | None = None


async def _resolve_workbench_experiment_id(campaign_id: int, row_id: int) -> int | None:
    """Map a workbench row (campaign_id + row_id) to its training ``ExperimentRow.id``.

    The bridge is the ``label`` column: workbench ingest stamps Completed rows
    with ``wb:{campaign_id}:{item_id}`` and the training store persists that
    label on ``ExperimentRow``. If the row has not been ingested yet (not saved
    or not Completed), there is no ExperimentRow and we return None.
    """
    from ..db.campaign_store import get_campaign_store
    from ..db.database import default_session_factory
    from ..db.models import ExperimentRow
    from ..services.workbench_training import workbench_record_label

    rows = await get_campaign_store().list_rows(campaign_id)
    match = next((r for r in rows if r.id == row_id), None)
    if match is None:
        return None
    label = workbench_record_label(campaign_id, match.item_id)
    with default_session_factory()() as session:
        row = (
            session.query(ExperimentRow)
            .filter(ExperimentRow.label == label)
            .first()
        )
        return row.id if row is not None else None


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

    filename = file.filename or "upload"

    # Size cap = Settings.ingest_max_upload_bytes (A11), enforced while reading.
    # Upload to Datalab ELN (best-effort; falls back to local file storage)
    content = await read_upload_capped(file, filename)
    item_id = await _datalab_item_id_for(experiment_id=experiment_id)
    source_document_id = await _upload_or_store_locally(content, filename, item_id=item_id)
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

    # P4.2: optional dossier S7/S5 patch after attachment upload (default OFF).
    try:
        from ..services.wiki.dossier import notify_dossier_event_for_experiment

        notify_dossier_event_for_experiment(experiment_id, "attachment_uploaded")
    except Exception:
        pass

    return AttachmentResponse(
        id=attachment_id,
        experiment_id=experiment_id,
        source_document_id=source_document_id,
        kind=kind,
        filename=filename,
        note=note,
        created_at=None,
    )


@router.get("/experiments/workbench/{campaign_id}/rows/{row_id}/attachments",
            response_model=list[AttachmentResponse])
async def get_workbench_row_attachments(
    campaign_id: int,
    row_id: int,
) -> list[AttachmentResponse]:
    """List attachments for a workbench row, resolved via its training experiment."""
    experiment_id = await _resolve_workbench_experiment_id(campaign_id, row_id)
    if experiment_id is None:
        return []
    from ..db.measurement_store import get_measurement_store

    store = get_measurement_store()
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
        for a in store.attachments_for(experiment_id)
    ]


@router.post(
    "/experiments/workbench/{campaign_id}/rows/{row_id}/versions/{version_id}/restore",
    response_model=dict,
)
async def restore_workbench_row_version(
    campaign_id: int,
    row_id: int,
    version_id: str,
) -> dict:
    """P3: restore a DOE row's DataLab item to a saved version.

    The platform mints a new "restored" version afterwards, so the operation is
    reversible — restoring again to any earlier version stays possible.
    """
    from ..config import get_settings
    from ..db.campaign_store import get_campaign_store
    from ..db.datalab_client import restore_item_version

    rows = await get_campaign_store().list_rows(campaign_id)
    match = next((r for r in rows if r.id == row_id), None)
    if match is None or not getattr(match, "refcode", None):
        raise HTTPException(status_code=404, detail="该行无 DataLab 版本记录")
    ok = await run_in_threadpool(
        restore_item_version,
        get_settings().datalab_api_url,
        match.refcode,
        version_id,
    )
    if not ok:
        raise HTTPException(status_code=502, detail="DataLab 版本恢复失败")
    return {"restored": True, "refcode": match.refcode}


@router.get(
    "/experiments/workbench/{campaign_id}/rows/{row_id}/versions",
    response_model=dict,
)
async def workbench_row_versions(
    campaign_id: int,
    row_id: int,
    compare_v1: str = Query(default=""),
    compare_v2: str = Query(default=""),
) -> dict:
    """P3: version history of a DOE row's DataLab item (auto-saved on every sync).

    Without compare params returns ``{refcode, versions: [{id, version, action,
    timestamp, creator}]}``; with ``compare_v1`` + ``compare_v2`` returns the
    DeepDiff between those two version ids under ``diff``.
    """
    from ..config import get_settings
    from ..db.campaign_store import get_campaign_store
    from ..db.datalab_client import diff_item_versions, list_item_versions

    rows = await get_campaign_store().list_rows(campaign_id)
    match = next((r for r in rows if r.id == row_id), None)
    if match is None or not getattr(match, "refcode", None):
        raise HTTPException(status_code=404, detail="该行无 DataLab 版本记录")

    settings = get_settings()
    assert match.refcode  # guard 上方已保证非空
    refcode = match.refcode
    if compare_v1 and compare_v2:
        diff = await run_in_threadpool(
            diff_item_versions, settings.datalab_api_url, refcode, compare_v1, compare_v2
        )
        return {"refcode": refcode, "diff": diff}
    versions = await run_in_threadpool(
        list_item_versions, settings.datalab_api_url, refcode
    )
    return {"refcode": refcode, "versions": versions}


@router.get(
    "/experiments/workbench/{campaign_id}/rows/{row_id}/attachments/{attachment_id}/download",
)
async def download_workbench_row_attachment(
    campaign_id: int,
    row_id: int,
    attachment_id: str,
) -> Response:
    """P4: download an attachment's original file.

    Prefers the DataLab ELN copy (attachment note carries ``[datalab:<file_id>]``);
    falls back to the local mirror stored under backend/data/attachments/ when the
    platform copy is unreachable or was never uploaded.
    """
    import re

    from ..db.datalab_client import get_file_bytes
    from ..db.measurement_store import get_measurement_store

    experiment_id = await _resolve_workbench_experiment_id(campaign_id, row_id)
    if experiment_id is None:
        raise HTTPException(status_code=404, detail="该行无实验记录")
    store = get_measurement_store()
    attachments = store.attachments_for(experiment_id)
    att = next((a for a in attachments if str(a.id) == attachment_id), None)
    if att is None:
        raise HTTPException(status_code=404, detail="附件不存在")

    # Original filename from the stored source document (local SSOT).
    filename = att.source_document_id or "attachment"
    from ..db.database import default_session_factory
    from ..db.models import SourceDocument

    with default_session_factory()() as session:
        doc = session.get(SourceDocument, att.source_document_id)
        if doc is not None and doc.filename:
            filename = doc.filename

    # 1) DataLab ELN copy
    from ..config import get_settings

    m = re.search(r"\[datalab:([0-9a-f]{24})\]", att.note or "")
    if m:
        content = await run_in_threadpool(
            get_file_bytes,
            get_settings().datalab_api_url,
            m.group(1),
            filename,
        )
        if content is not None:
            return Response(
                content=content,
                media_type="application/octet-stream",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

    # 2) local mirror fallback (data/attachments/{local-<hash>}-{uuid}-{name})
    base = Path(get_settings().db_url.replace("sqlite:///", "")).parent / "attachments"
    if base.is_dir():
        prefix = f"{att.source_document_id}-"
        for candidate in base.iterdir():
            if candidate.is_file() and candidate.name.startswith(prefix):
                return Response(
                    content=candidate.read_bytes(),
                    media_type="application/octet-stream",
                    headers={
                        "Content-Disposition": f'attachment; filename="{filename}"'
                    },
                )
    raise HTTPException(status_code=404, detail="文件不可用（平台与本机均无副本）")


@router.delete(
    "/experiments/workbench/{campaign_id}/rows/{row_id}/attachments/{attachment_id}",
)
async def delete_workbench_row_attachment(
    campaign_id: int,
    row_id: int,
    attachment_id: str,
) -> dict:
    """P4: unbind an attachment from the row.

    Local attachment row is deleted; the DataLab ELN copy (if any) is kept for
    traceability (platform delete-file API is out of sync with its storage —
    see platform-maximize plan §P4).
    """
    from ..db.measurement_store import get_measurement_store

    experiment_id = await _resolve_workbench_experiment_id(campaign_id, row_id)
    if experiment_id is None:
        raise HTTPException(status_code=404, detail="该行无实验记录")
    store = get_measurement_store()
    attachments = store.attachments_for(experiment_id)
    if not any(str(a.id) == attachment_id for a in attachments):
        raise HTTPException(status_code=404, detail="附件不存在")
    if not store.delete_attachment(attachment_id):
        raise HTTPException(status_code=500, detail="附件删除失败")
    # Refresh S7/S5 when an attachment is removed (auto_patch default OFF).
    try:
        from ..services.wiki.dossier import notify_dossier_event_for_campaign

        notify_dossier_event_for_campaign(campaign_id, "attachment_uploaded")
    except Exception:
        pass
    return {"deleted": True}


@router.post("/experiments/workbench/{campaign_id}/rows/{row_id}/attachments",
             response_model=AttachmentResponse)
async def upload_workbench_row_attachment(
    campaign_id: int,
    row_id: int,
    file: UploadFile = File(...),
    kind: str = Query(default="qc_report"),
    note: str = Query(default=""),
) -> AttachmentResponse:
    """Upload an attachment for a workbench row, resolving its training experiment.

    The row must already be ingested as training data (saved + Completed with
    measurements); otherwise there is no experiment to bind the file to.
    """
    experiment_id = await _resolve_workbench_experiment_id(campaign_id, row_id)
    if experiment_id is None:
        raise HTTPException(
            status_code=409,
            detail="该实验行尚未回灌为训练数据——请先保存台账（Completed 且有实测值）",
        )
    from ..db.measurement_store import get_measurement_store

    filename = file.filename or "upload"
    content = await read_upload_capped(file, filename)
    item_id = await _datalab_item_id_for(campaign_id=campaign_id, row_id=row_id)
    source_document_id = await _upload_or_store_locally(content, filename, item_id=item_id)
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

    # P4.2: optional dossier S7/S5 patch after workbench attachment upload.
    try:
        from ..services.wiki.dossier import notify_dossier_event_for_campaign

        notify_dossier_event_for_campaign(campaign_id, "attachment_uploaded")
    except Exception:
        pass

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


class NoteUpdateRequest(BaseModel):
    note: str | None = None


@router.put("/experiments/workbench/{campaign_id}/rows/{row_id}/tags")
async def update_row_tags(
    campaign_id: int,
    row_id: int,
    body: TagUpdateRequest,
    request: Request,
) -> WorkbenchRowResponse:
    """Set tags on a workbench row (field-only; avoids full-row sync races)."""
    from ..middleware.api_auth import assert_owner, get_current_owner

    store = get_campaign_store()
    campaign = await store.get_campaign(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    assert_owner(getattr(campaign, "owner_id", None), get_current_owner(request))
    # 只更新 tags 字段，不回写整行 —— 避免覆盖并发编辑的其他字段（A9）
    fresh = await store.set_row_tags(campaign_id, row_id, body.tags)
    if fresh is None:
        raise HTTPException(status_code=404, detail="Row not found")
    return _row_response(fresh)


@router.put("/experiments/workbench/{campaign_id}/rows/{row_id}/note")
async def update_row_note(
    campaign_id: int,
    row_id: int,
    body: NoteUpdateRequest,
    request: Request,
) -> WorkbenchRowResponse:
    """Set note on a workbench row (field-only; avoids full-row sync races)."""
    from ..middleware.api_auth import assert_owner, get_current_owner

    store = get_campaign_store()
    campaign = await store.get_campaign(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    assert_owner(getattr(campaign, "owner_id", None), get_current_owner(request))
    fresh = await store.set_row_note(campaign_id, row_id, body.note)
    if fresh is None:
        raise HTTPException(status_code=404, detail="Row not found")
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


@router.get("/experiments/search", response_model=list[ExperimentSearchResult], include_in_schema=False)
async def search_experiments(
    q: str = Query(default="", description="搜索关键词"),
) -> list[ExperimentSearchResult]:
    """Search across all campaigns by keyword (tags, params, measurements)."""
    # In Datalab mode: delegate to Datalab search API
    settings = get_settings()
    backend = (settings.campaign_backend or "sqlite").lower()

    if backend in ("datalab", "auto"):
        from ..db.datalab_client import check_datalab_reachable, datalab_headers
        ok, _ = await run_in_threadpool(check_datalab_reachable, settings.datalab_api_url, timeout=2.0)
        if ok:
            import httpx
            try:
                async with httpx.AsyncClient(
                    base_url=settings.datalab_api_url.rstrip("/"),
                    timeout=10.0,
                    headers=datalab_headers(),
                ) as client:
                    resp = await client.get("/search/", params={"q": q})
                    if resp.status_code < 400:
                        return _parse_datalab_search(resp.json())
            except Exception as exc:
                logger.debug("datalab search failed, falling back to local scan: %s", exc)

    # Local SQLite scan — P-8: filter refs in SQL via json_each/json_extract
    # instead of pulling every campaign's full sample_refs JSON into Python.
    # The SQL predicate mirrors the old Python one: q (case-insensitive) is a
    # substring of " ".join(tags) + " " + note. json_extract unescapes JSON
    # string escaping, so CJK keywords match correctly (a LIKE on the raw JSON
    # text would miss ensure_ascii-escaped characters).
    results: list[ExperimentSearchResult] = []
    from ..db.database import default_session_factory
    with default_session_factory()() as session:
        je = func.json_each(Campaign.sample_refs).table_valued("key", "value")
        tags_each = func.json_each(
            func.json_extract(je.c.value, "$.tags")
        ).table_valued("value")
        tags_concat = (
            select(func.group_concat(tags_each.c.value, " "))
            .select_from(tags_each)
            .scalar_subquery()
        )
        ref_text = func.lower(
            func.coalesce(tags_concat, "")
            + " "
            + func.coalesce(func.json_extract(je.c.value, "$.note"), "")
        )
        stmt = (
            select(Campaign.id, Campaign.name, je.c.value)
            .select_from(Campaign)
            .join(je, true())
        )
        if q:
            stmt = stmt.where(func.instr(ref_text, q.lower()) > 0)
        for camp_id, camp_name, ref_json in session.execute(stmt):
            try:
                ref = json.loads(ref_json) if isinstance(ref_json, str) else {}
            except (TypeError, ValueError):
                continue
            if not isinstance(ref, dict):
                continue
            # 空 q：返回全部行（与 Datalab 搜索路径一致，A12 行为不一致修复）
            if q:
                tags = " ".join(ref.get("tags") or [])
                note = str(ref.get("note") or "")
                text = f"{tags} {note}".lower()
                if q.lower() not in text:
                    continue
            # ref["id"] 可能缺失：容错跳过而非 KeyError
            rid = ref.get("id")
            if rid is None:
                continue
            try:
                row_id = int(rid)
            except (TypeError, ValueError):
                continue
            results.append(ExperimentSearchResult(
                row_id=row_id,
                campaign_id=camp_id,
                campaign_name=camp_name,
                item_id=str(ref.get("item_id", "")),
                status=str(ref.get("status", "Pending")),
                planned_params=ref.get("planned_params", {}),
                measurements=ref.get("measurements", {}),
            ))
    return results


def _parse_datalab_search(body: list[dict]) -> list[ExperimentSearchResult]:
    """Map Datalab search hits onto local campaign/row ids when possible.

    Previously always returned campaign_id=0 / row_id=0, which broke
    Workbench「打开检索命中」→ selectWorkbenchCampaign(0).
    """
    import re

    index: dict[str, tuple[int, int, str]] = {}
    try:
        from ..db.database import default_session_factory

        with default_session_factory()() as session:
            # P-8: 只把 (item_id, id, campaign) 三列拉回 Python，而非全表
            # Campaign ORM + 完整 sample_refs JSON。item_id 的空值过滤在 SQL
            # 侧完成（trim 后长度 > 0），语义与旧 Python 循环一致。
            je = func.json_each(Campaign.sample_refs).table_valued("value")
            item_id_expr = func.json_extract(je.c.value, "$.item_id")
            stmt = (
                select(
                    item_id_expr.label("item_id"),
                    func.json_extract(je.c.value, "$.id").label("rid"),
                    Campaign.id,
                    Campaign.name,
                )
                .select_from(Campaign)
                .join(je, true())
                .where(
                    func.length(
                        func.trim(func.coalesce(item_id_expr, ""))
                    )
                    > 0
                )
            )
            for item_id_raw, rid, camp_id, camp_name in session.execute(stmt):
                item_id = str(item_id_raw or "").strip()
                if not item_id:
                    continue
                try:
                    row_id = int(rid) if rid is not None else 0
                except (TypeError, ValueError):
                    row_id = 0
                index[item_id] = (int(camp_id), row_id, str(camp_name or ""))
    except Exception as exc:  # pragma: no cover
        logger.debug("datalab search id map failed: %s", exc)

    pat = re.compile(r"^formumind_c(\d+)_r(\d+)_")
    results: list[ExperimentSearchResult] = []
    for sample in body:
        if not isinstance(sample, dict):
            continue
        blocks = sample.get("blocks_obj", {}) or {}
        params_block = blocks.get("formumind_params", {}) or {}
        meas_block = blocks.get("formumind_measurements", {}) or {}
        item_id = str(sample.get("item_id") or "")
        campaign_id, row_id, campaign_name = 0, 0, ""
        if item_id in index:
            campaign_id, row_id, campaign_name = index[item_id]
        else:
            m = pat.match(item_id)
            if m:
                campaign_id = int(m.group(1))
                row_id = int(m.group(2))
        # Prefer nested planned/actual if block uses workbench shape
        pdata = params_block.get("data", {}) if isinstance(params_block, dict) else {}
        planned = pdata.get("planned_params") if isinstance(pdata, dict) else None
        if not isinstance(planned, dict):
            planned = pdata if isinstance(pdata, dict) else {}
        mdata = meas_block.get("data", {}) if isinstance(meas_block, dict) else {}
        measurements = mdata if isinstance(mdata, dict) else {}
        if isinstance(pdata, dict) and pdata.get("status"):
            status = str(pdata.get("status"))
        else:
            status = str(sample.get("status", "Pending"))
        results.append(
            ExperimentSearchResult(
                row_id=row_id,
                campaign_id=campaign_id,
                campaign_name=campaign_name,
                item_id=item_id,
                status=status,
                planned_params=planned,
                measurements=measurements,
            )
        )
    return results


# ── Phase 3.3: convergence webhook receiver ───────────────────────────────

class ConvergenceWebhookPayload(BaseModel):
    campaign_id: int
    converged: bool = True
    round_count: int = 0
    message: str = ""


@router.post("/experiments/hooks/convergence", include_in_schema=False)
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


# ── Round-grouped history view ────────────────────────────────────────────────


class CampaignRoundsResponse(BaseModel):
    rounds: list[dict]
    total_rounds: int
    page: int
    page_size: int
    unassociated_ledger: int


def _campaign_rounds_data(campaign_id: int) -> tuple[list[dict], int]:
    """组装 campaign 按 round 分组视图，返回 (rounds, unassociated_ledger)。

    每个 round: {round, loop_entry, doe_plan, ledger_rows}
    - loop_entry: loop_history 该轮收敛分析（含 doe_plan_id）
    - doe_plan: 该轮 DOE（doe_plans 按 round 匹配）
    - ledger_rows: 该轮台账行（按 created_at 时间窗口推断：落在某轮 loop 收敛
      时间戳之前、上一轮之后的，归该轮）
    """
    from ..db import doe_plan_store
    from ..db.database import default_session_factory
    from ..db.models import ExperimentRow

    campaign = get_campaign_store().get_campaign_sync(campaign_id)
    if campaign is None:
        return [], 0

    loop_history = list(campaign.loop_history or [])
    loop_ats = [e.get("at") for e in loop_history if e.get("at")]

    factory = default_session_factory()
    with factory() as session:
        doe_items, _ = doe_plan_store.list_history(
            session,
            campaign_id=campaign_id,
            project_id=getattr(campaign, "project_id", None) or None,
            page=1,
            page_size=1000,
        )
    doe_by_round: dict[int, dict] = {
        d["round"]: d for d in doe_items if d.get("round") is not None
    }

    prefix = f"wb:{campaign_id}:"
    factory = default_session_factory()
    with factory() as session:
        exp_rows = (
            session.query(ExperimentRow)
            .filter(ExperimentRow.label.like(f"{prefix}%"))
            .all()
        )
    ledger = [
        {
            "item_id": (
                r.item_id or (r.label[len(prefix):] if r.label.startswith(prefix) else "")
            ),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "measured": r.measured,
        }
        for r in exp_rows
    ]

    rounds = [
        {
            "round": idx,
            "loop_entry": entry,
            "doe_plan": doe_by_round.get(idx),
            "ledger_rows": [],
        }
        for idx, entry in enumerate(loop_history, start=1)
    ]

    unassociated = 0
    for lr in ledger:
        cat = lr.get("created_at")
        assigned = None
        if cat and loop_ats:
            try:
                cat_dt = _parse_iso(cat)
            except ValueError:
                cat_dt = None
            if cat_dt is not None:
                for i, at in enumerate(loop_ats):
                    at_dt = _parse_iso(at)
                    if at_dt is not None and cat_dt <= at_dt:
                        assigned = i
                        break
        if assigned is not None:
            rounds[assigned]["ledger_rows"].append(lr)
        else:
            unassociated += 1

    return rounds, unassociated


def _parse_iso(s: str) -> datetime | None:
    """解析 ISO 时间戳为 datetime（兼容 'Z' 与 '+00:00' 后缀，统一到可比较对象）。"""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


@router.get(
    "/experiments/workbench/{campaign_id}/rounds",
    response_model=CampaignRoundsResponse,
)
def campaign_rounds(
    campaign_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(5, ge=1, le=20),
) -> CampaignRoundsResponse:
    """按 round 分页查看 DOE + 台账（时间窗口推断）。"""
    rounds, unassociated = _campaign_rounds_data(campaign_id)
    total = len(rounds)
    start = (page - 1) * page_size
    return CampaignRoundsResponse(
        rounds=rounds[start:start + page_size],
        total_rounds=total,
        page=page,
        page_size=page_size,
        unassociated_ledger=unassociated,
    )


# ── Pause/Resume DOE cycle hooks ─────────────────────────────────────────
@router.post("/experiments/hooks/pause-doecycle/{campaign_id}", response_model=Dict[str, str])
def pause_doecyle(
    campaign_id: int,
    payload: Dict[str, bool],
) -> Dict[str, str]:
    """Pause or resume a DOE cycle for a campaign.

    Expected payload: {"isPaused": true/false}
    """
    from ..services.workbench_loop import pause_resume_doecyle

    is_paused = payload.get("isPaused", False)
    success = pause_resume_doecyle(campaign_id, is_paused)

    if success:
        return {"status": "success", "message": f"DOE cycle {'paused' if is_paused else 'resumed'} for campaign {campaign_id}"}
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to {'pause' if is_paused else 'resume'} DOE cycle for campaign {campaign_id}",
        )


@router.get("/experiments/hooks/doecyle-status/{campaign_id}", response_model=Dict[str, Any])
def get_doecyle_status(
    campaign_id: int,
) -> Dict[str, Any]:
    """Get the current status of a DOE cycle for a campaign.

    Returns: {"isPaused": bool, "lastUpdated": str, "campaignId": int, ...}
    """
    from ..services.workbench_loop import get_doecyle_status as _impl

    status_val = _impl(campaign_id)
    if status_val is None:
        # Redis unavailable — still answer so the UI can poll without hard failure.
        return {"isPaused": False, "lastUpdated": None, "campaignId": campaign_id, "degraded": True}
    return status_val


# ── P0-2: Datalab -> 优化器手动同步 ──────────────────────────────────────────


class SyncDatalabRequest(BaseModel):
    """POST /experiments/{id}/sync-datalab 请求体。

    ``datalab_item_id``: 本次同步要拉取的 Datalab item。行保持本地训练记录
    身份 (``item_id`` 不被改写), 因此同步到的 lab 测量对
    ``SqlExperimentStore`` (只读 ``item_id IS NULL``) 可见, 下一轮 DOE 的
    ``load_prior_measurements()`` 能吃到 lab 实测。若省略, 则回退使用行上
    已绑定的 ``item_id`` (若行上也没有 -> 409)。
    """

    datalab_item_id: str | None = Field(
        default=None, description="本次同步的 Datalab item id; 省略则用行上绑定的 item_id"
    )


@router.post("/experiments/{experiment_id}/sync-datalab", response_model=Dict[str, Any])
def sync_experiment_from_datalab(
    experiment_id: int, body: SyncDatalabRequest | None = None
) -> Dict[str, Any]:
    """手动触发单个实验的 Datalab -> MeasurementStore 同步 (P0-2)。

    流程: 由 experiment_id 查 ExperimentRow, 取本次同步的 Datalab item_id
    (请求体 ``datalab_item_id`` 优先, 否则行上绑定的 ``item_id``) -> 拉取
    Datalab ``formumind_measurements`` 块 -> 校验块内 experiment_id 与调用方
    一致 -> 写入 MeasurementStore 明细并回写 ``experiments.measured`` ->
    成功后刷新训练 registry。

    可见性语义: 行的 ``item_id`` 不被改写。本地训练记录
    (``item_id IS NULL``) 同步后仍对 ``SqlExperimentStore`` 可见, 下一轮
    DOE 的 ``load_prior_measurements()`` 可见 lab 实测; 已绑定 Datalab 的行
    仍归 Datalab store 管 (Datalab 可达时 live 读)。

    Fail-open: Datalab 不可达返回 ``{"synced": 0, "errors": [...]}`` 而不是
    500; ``validated`` 恒为 None, 真实端到端验证见 scripts/verify_datalab.py。
    """
    from ..db.database import default_session_factory
    from ..db.models import ExperimentRow
    from ..services.datalab_sync import (
        block_experiment_id,
        fetch_item_data,
        sync_item_data_to_store,
    )

    factory = default_session_factory()
    with factory() as session:
        row = session.get(ExperimentRow, experiment_id)
        bound_item_id = row.item_id if row else None
        # U-4: 强成功层关联需要的快照（session 关闭后 row 即 detached）。
        exp_project_id = (row.project_id or None) if row else None
        exp_factors = dict(row.factors or {}) if row else {}
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"experiment {experiment_id} not found",
        )
    requested = (body.datalab_item_id if body else None) or ""
    requested = requested.strip()
    item_id = requested or bound_item_id
    if not item_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"experiment {experiment_id} has no Datalab item_id; nothing to sync",
        )

    settings = get_settings()
    item_data = fetch_item_data(
        settings.datalab_api_url, item_id, timeout=settings.datalab_timeout_seconds
    )
    claimed = block_experiment_id(item_data)
    if claimed is not None and claimed != experiment_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"datalab item {item_id} block claims experiment_id={claimed}, "
                f"but caller asked for {experiment_id}"
            ),
        )
    report = sync_item_data_to_store(
        item_data, experiment_id, item_id=item_id
    )
    if report.get("synced"):
        # 注意: 不回写 row.item_id。行的本地训练记录身份保持不变,
        # SqlExperimentStore 的 item_id IS NULL 过滤语义不受影响,
        # 这正是同步到的 lab 数据对优化器可见的前提。
        try:
            registry.load()
            refreshed = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("registry refresh after datalab sync failed: %s", exc)
            refreshed = False
    else:
        refreshed = False
    report["registry_refreshed"] = refreshed
    report["experiment_id"] = experiment_id
    report["datalab_item_id"] = item_id
    # U-4: 强成功层回写 —— 同步出 lab 测量后，启发式关联被采纳的推荐配方。
    # fail-open：关联不上是正常情况，绝不影响同步结果本身。
    if report.get("synced"):
        try:
            from ..db import recommend_outcome_store
            from ..db.session_utils import commit_session

            with factory() as vsession:
                vrow = vsession.get(ExperimentRow, experiment_id)
                measured_keys = list((vrow.measured or {}).keys()) if vrow else []
            with commit_session(factory) as vsession:
                validated_rid = recommend_outcome_store.try_mark_experiment_validated(
                    vsession,
                    project_id=exp_project_id,
                    factors=exp_factors,
                    experiment_id=experiment_id,
                    measured_keys=measured_keys,
                )
            report["recommend_validated"] = validated_rid
        except Exception as exc:  # noqa: BLE001 - fail-open
            logger.warning("U-4 experiment validation write skipped: %s", exc)
    return report

"""POST /api/qc/analyze — coating defect QC (reserved skeleton).

Computer-vision defect classification (cracks, blistering, rust) belongs to the
downstream quality-control stage rather than upstream formulation design, and is
best deployed as an independent microservice (FastAPI + a PyTorch model in its
own Docker image). This endpoint defines the request/response contract so the
frontend can integrate against a stable shape; it returns a placeholder result
until a real model is wired in. Kept intentionally dependency-free.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()


class QCRequest(BaseModel):
    image_url: str
    notes: str = ""


class Defect(BaseModel):
    label: str  # e.g. "crack", "blister", "rust", "orange_peel"
    confidence: float
    bbox: list[float] = []  # [x, y, w, h] in pixels, optional


class QCResponse(BaseModel):
    defects: list[Defect]
    engine: str = "placeholder"
    message: str = ""


@router.post("/qc/analyze", response_model=QCResponse)
def analyze(req: QCRequest) -> QCResponse:
    """Reserved: returns an empty defect list until a CV model is deployed."""
    return QCResponse(
        defects=[],
        engine="placeholder",
        message="视觉质检为预留接口；未来由独立的 torch/torchvision 微服务提供缺陷检测。",
    )


# ── QC report ingest ─────────────────────────────────────────────────────────
# Distinct from /qc/analyze above, which stays a defect-detection placeholder.
# This is the report pipeline: a test certificate becomes typed measurements
# bound to the experiment it reports on.


class QCMeasurementView(BaseModel):
    metric: str
    value: float
    unit: str = ""
    test_method: str = ""
    spec_min: float | None = None
    spec_max: float | None = None
    passed: bool | None = None


class QCReportResponse(BaseModel):
    experiment_id: int
    source_id: str
    measurements: list[QCMeasurementView] = Field(default_factory=list)
    measurement_count: int = 0
    attached: bool = True
    already_attached: bool = False
    synced_measured: dict[str, float] = Field(default_factory=dict)
    report_meta: dict = Field(default_factory=dict)
    parser: str = ""
    extraction_error: str | None = None
    message: str = ""


class QCMeasurementListResponse(BaseModel):
    experiment_id: int
    measurements: list[QCMeasurementView] = Field(default_factory=list)
    attachments: list[dict] = Field(default_factory=list)


@router.post("/qc/report", response_model=QCReportResponse)
async def ingest_qc_report(
    file: UploadFile = File(...),
    experiment_id: int = Form(default=0),
    campaign_id: int = Form(default=0),
    row_id: int = Form(default=0),
    project_id: str = Form(default=""),
    sync_measured: bool = Form(default=True),
) -> QCReportResponse:
    """Upload a test report and bind its results to an experiment.

    The report is stored, its measurements extracted with unit / test method /
    spec window, and both bound to the experiment in one transaction. With
    ``sync_measured`` the values are folded into the experiment's flat
    ``measured`` column, which is what makes them training data.
    """
    from ..config import get_settings
    from ..db.database import default_session_factory
    from ..services.parsing import parse_document
    from ..services.qc_ingest import ingest_qc_report_tx, sync_measurements_to_experiment

    content = await file.read()
    filename = file.filename or "qc_report"
    limit = get_settings().ingest_max_upload_bytes
    if len(content) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"File {filename!r} exceeds upload limit ({limit // (1024 * 1024)} MiB)",
        )
    if not content:
        raise HTTPException(status_code=400, detail="空文件")

    # Resolve the binding target: a workbench row (campaign_id + row_id) is the
    # new source of truth for report binding; fall back to a legacy experiment_id
    # for backward compatibility.
    if campaign_id > 0 and row_id > 0:
        from ..services.workbench_training import ensure_experiment_for_row

        try:
            experiment_id = await run_in_threadpool(
                ensure_experiment_for_row, campaign_id, row_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    ext = Path(filename).suffix.lower().lstrip(".")
    # parse_document and the LLM extraction are both blocking.
    parsed = await run_in_threadpool(parse_document, content, ext)
    if not parsed.ok:
        raise HTTPException(
            status_code=422, detail=f"无法从 {filename!r} 提取文本（格式：{ext or '未知'}）"
        )

    try:
        result = await run_in_threadpool(
            ingest_qc_report_tx,
            default_session_factory(),
            experiment_id=experiment_id,
            filename=filename,
            text=parsed.markdown,
            title=Path(filename).stem,
            project_id=project_id or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("qc report ingest failed")
        raise HTTPException(status_code=500, detail="检测报告入库失败") from exc

    synced: dict[str, float] = {}
    if sync_measured and result.measurements:
        synced = await run_in_threadpool(sync_measurements_to_experiment, experiment_id)

    # Phase 2.6: mirror the raw report bytes into Datalab ELN (best-effort).
    # The report text + measurements are already persisted locally; this uploads
    # the original file so the certificate is traceable in the ELN too, and
    # records the ELN file id on the attachment.
    try:
        from ..db.datalab_client import upload_file as _datalab_upload_file
        from ..db.measurement_store import get_measurement_store as _get_ms

        _file_id = await _datalab_upload_file(
            get_settings().datalab_api_url, content, filename
        )
        if _file_id:
            _get_ms().set_attachment_datalab_ref(
                experiment_id, result.source_id, _file_id
            )
    except Exception:
        logger.warning("qc report Datalab archival failed", exc_info=True)

    message = ""
    if not result.measurements:
        message = (
            "未从报告中提取到检测项——报告已入库并绑定，但没有可用数值"
            "（未配置 LLM API key 时属预期行为）"
        )
    return QCReportResponse(
        experiment_id=experiment_id,
        source_id=result.source_id,
        measurements=[QCMeasurementView(**m.model_dump()) for m in result.measurements],
        measurement_count=result.measurement_count,
        attached=True,
        already_attached=result.already_attached,
        synced_measured=synced,
        report_meta=result.report_meta,
        parser=parsed.parser,
        extraction_error=result.extraction_error,
        message=message,
    )


@router.get("/qc/experiments/{experiment_id}/measurements", response_model=QCMeasurementListResponse)
def experiment_measurements(experiment_id: int) -> QCMeasurementListResponse:
    """Typed measurements and bound reports for one experiment."""
    from ..db.measurement_store import get_measurement_store

    store = get_measurement_store()
    return QCMeasurementListResponse(
        experiment_id=experiment_id,
        measurements=[
            QCMeasurementView(**m.model_dump()) for m in store.for_experiment(experiment_id)
        ],
        attachments=[
            {
                "id": a.id,
                "source_document_id": a.source_document_id,
                "kind": a.kind,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in store.attachments_for(experiment_id)
        ],
    )

"""POST /api/ingest — Local file upload, URL fetch, pasted text, and batch upload."""
from __future__ import annotations

from datetime import datetime
import hashlib
import logging
import os
import shutil
import tempfile
import time

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from ..config import get_settings
from ..domain.schemas import Evidence, SourceGuideSchema
from ..services import colbert_store
from ..services.ingestion import ingest_file, ingest_files_batch, ingest_text, ingest_url
from ..services.parsing import ParserUnavailable
from ..db.source_store import get_source_store
from ..worker.tasks import dispatch_file_ingest
from .tasks import accepted_response

logger = logging.getLogger(__name__)

router = APIRouter()


class IngestResponse(BaseModel):
    filename: str
    evidence: list[Evidence]
    total: int
    source_id: str | None = None
    source_guide: SourceGuideSchema | None = None
    extraction_status: str = "skipped"


class BatchIngestResponse(BaseModel):
    evidence: list[Evidence]
    total: int
    files_processed: int
    source_id: str | None = None
    extraction_status: str = "skipped"


class SourceDocumentResponse(BaseModel):
    id: str
    filename: str
    title: str
    source_kind: str
    raw_text_chars: int
    source_guide: SourceGuideSchema | None = None
    extraction_status: str
    extraction_error: str | None = None
    created_at: datetime


class IngestUrlRequest(BaseModel):
    url: str = Field(min_length=8)


class IngestTextRequest(BaseModel):
    text: str = Field(min_length=1)
    title: str = ""


class IngestTaskRequest(BaseModel):
    """统一摄取入口: 按 identifier 拉全文入库(2026-09-05 P1)."""
    doc_type: str = Field(pattern="^(patent|paper|web)$")
    identifier: str = Field(min_length=3)


def _enforce_upload_size(content: bytes, filename: str) -> None:
    limit = get_settings().ingest_max_upload_bytes
    if len(content) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"File {filename!r} exceeds upload limit ({limit // (1024 * 1024)} MiB)",
        )


def _to_ingest_response(filename: str, outcome) -> IngestResponse:
    return IngestResponse(
        filename=filename,
        evidence=outcome.evidence,
        total=len(outcome.evidence),
        source_id=outcome.source_id,
        source_guide=outcome.source_guide,
        extraction_status=outcome.extraction_status,
    )


def _write_upload(content: bytes, filename: str, dest_dir: str) -> str:
    """Persist one uploaded part to the temp dir the task will read from.

    The name is flattened to a basename so a crafted filename cannot escape
    the temp directory; the original name is still what the parser sniffs.
    """
    safe_name = os.path.basename(filename) or "upload"
    path = os.path.join(dest_dir, safe_name)
    with open(path, "wb") as fh:
        fh.write(content)
    return path


@router.post("/ingest")
async def ingest_document(file: UploadFile = File(...)):
    """Queue a single-file ingest: 202 + task_id, results via SSE/poll.

    Parsing (OCR on a scan) can run for minutes; doing it inside the request
    is what let a proxy in front of uvicorn cut the connection and show the
    user a 502 for work that had actually completed.
    """
    started = time.time()
    content = await file.read()
    filename = file.filename or "upload"
    _enforce_upload_size(content, filename)

    upload_dir = tempfile.mkdtemp(prefix="formumind_upload_")
    path = _write_upload(content, filename, upload_dir)
    task_id = dispatch_file_ingest([{"name": filename, "path": path}], upload_dir=upload_dir)
    if not task_id:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise HTTPException(
            status_code=503,
            detail="后台任务队列不可用，无法提交解析任务（请检查 Redis / celery worker）",
        )
    logger.info(
        "Ingest queued: %s (%d KiB) in %.0fms → task %s",
        filename, len(content) // 1024, (time.time() - started) * 1000, task_id,
    )
    return accepted_response(task_id, "file_ingest")


@router.post("/ingest/batch")
async def ingest_batch(files: list[UploadFile] = File(...)):
    """Queue a multi-file ingest: 202 + task_id, results via SSE/poll."""
    started = time.time()
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > 20:
        raise HTTPException(status_code=413, detail="最多同时上传20个文件")

    upload_dir = tempfile.mkdtemp(prefix="formumind_upload_")
    queued: list[dict] = []
    seen: set[str] = set()
    try:
        for f in files:
            content = await f.read()
            name = f.filename or "upload"
            _enforce_upload_size(content, name)
            digest = hashlib.sha256(content).hexdigest()
            if digest in seen:  # byte-identical file picked twice in one dialog
                continue
            seen.add(digest)
            queued.append({"name": name, "path": _write_upload(content, name, upload_dir)})
    except HTTPException:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise

    task_id = dispatch_file_ingest(queued, upload_dir=upload_dir)
    if not task_id:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise HTTPException(
            status_code=503,
            detail="后台任务队列不可用，无法提交解析任务（请检查 Redis / celery worker）",
        )
    logger.info(
        "Ingest batch queued: %d file(s) in %.0fms → task %s",
        len(queued), (time.time() - started) * 1000, task_id,
    )
    return accepted_response(task_id, "file_ingest")


@router.post("/ingest/url", response_model=IngestResponse)
def ingest_from_url(req: IngestUrlRequest):
    try:
        outcome = ingest_url(req.url.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("ingest_url failed")
        raise HTTPException(status_code=502, detail="文件处理失败") from exc
    colbert_store.index_evidence(outcome.evidence)
    return _to_ingest_response(req.url, outcome)


@router.post("/ingest/text", response_model=IngestResponse)
def ingest_from_text(req: IngestTextRequest):
    title = req.title or "Pasted text"
    outcome = ingest_text(req.text, title)
    colbert_store.index_evidence(outcome.evidence)
    return _to_ingest_response(title, outcome)


@router.post("/ingest/task", response_model=IngestResponse)
def ingest_from_task(req: IngestTaskRequest):
    """DOI/arXiv id/专利号/URL → 全文 → 入库(编排见 services.document_task)."""
    from ..services.document_task import resolve_document

    try:
        outcome = resolve_document(req.doc_type, req.identifier.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if outcome.error:
        raise HTTPException(status_code=502, detail=outcome.error)
    colbert_store.index_evidence(outcome.evidence)
    return _to_ingest_response(outcome.identifier, outcome)


@router.get("/sources/{source_id}", response_model=SourceDocumentResponse, include_in_schema=False)
def get_source(source_id: str):
    row = get_source_store().get(source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Source not found")
    guide = None
    if row.source_guide:
        guide = SourceGuideSchema.model_validate(row.source_guide)
    return SourceDocumentResponse(
        id=row.id,
        filename=row.filename,
        title=row.title,
        source_kind=row.source_kind,
        raw_text_chars=row.raw_text_chars,
        source_guide=guide,
        extraction_status=row.extraction_status,
        extraction_error=row.extraction_error,
        created_at=row.created_at,
    )

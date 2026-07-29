"""POST /api/ingest — Local file upload, URL fetch, pasted text, and batch upload."""
from __future__ import annotations

from ..services.errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal
from datetime import datetime
import logging

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from ..config import get_settings
from ..domain.schemas import Evidence, SourceGuideSchema
from ..services import colbert_store
from ..services.ingestion import ingest_file, ingest_files_batch, ingest_text, ingest_url
from ..services.parsing import ParserUnavailable
from ..db.source_store import get_source_store

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


@router.post("/ingest", response_model=IngestResponse)
async def ingest_document(file: UploadFile = File(...)):
    content = await file.read()
    filename = file.filename or "upload"
    _enforce_upload_size(content, filename)
    # Parsing is synchronous and can run for seconds (docling) to minutes
    # (cloud escalation). On the event loop that stalls every other request.
    try:
        outcome = await run_in_threadpool(ingest_file, filename, content)
    except ParserUnavailable as exc:
        raise HTTPException(status_code=422, detail=exc.hint) from exc
    colbert_store.index_evidence(outcome.evidence)
    return _to_ingest_response(filename, outcome)


@router.post("/ingest/batch", response_model=BatchIngestResponse)
async def ingest_batch(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > 20:
        raise HTTPException(status_code=413, detail="最多同时上传20个文件")
    pairs: list[tuple[str, bytes]] = []
    for f in files:
        content = await f.read()
        name = f.filename or "upload"
        _enforce_upload_size(content, name)
        pairs.append((name, content))
    try:
        outcome = await run_in_threadpool(ingest_files_batch, pairs)
    except ParserUnavailable as exc:
        raise HTTPException(status_code=422, detail=exc.hint) from exc
    colbert_store.index_evidence(outcome.evidence)
    return BatchIngestResponse(
        evidence=outcome.evidence,
        total=len(outcome.evidence),
        files_processed=len(files),
        source_id=outcome.source_id,
        extraction_status=outcome.extraction_status,
    )


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


@router.get("/sources/{source_id}", response_model=SourceDocumentResponse)
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

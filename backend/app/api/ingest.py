"""POST /api/ingest — Local file upload, URL fetch, pasted text, and batch upload."""
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ..domain.schemas import Evidence
from ..services import colbert_store
from ..services.ingestion import ingest_file, ingest_files_batch, ingest_text, ingest_url

router = APIRouter()


class IngestResponse(BaseModel):
    filename: str
    evidence: list[Evidence]
    total: int


class BatchIngestResponse(BaseModel):
    evidence: list[Evidence]
    total: int
    files_processed: int


class IngestUrlRequest(BaseModel):
    url: str = Field(min_length=8)


class IngestTextRequest(BaseModel):
    text: str = Field(min_length=1)
    title: str = ""


@router.post("/ingest", response_model=IngestResponse)
async def ingest_document(file: UploadFile = File(...)):
    content = await file.read()
    evidence = ingest_file(file.filename or "upload", content)
    colbert_store.index_evidence(evidence)
    return IngestResponse(filename=file.filename or "upload", evidence=evidence, total=len(evidence))


@router.post("/ingest/batch", response_model=BatchIngestResponse)
async def ingest_batch(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    pairs: list[tuple[str, bytes]] = []
    for f in files:
        pairs.append((f.filename or "upload", await f.read()))
    evidence = ingest_files_batch(pairs)
    colbert_store.index_evidence(evidence)
    return BatchIngestResponse(evidence=evidence, total=len(evidence), files_processed=len(files))


@router.post("/ingest/url", response_model=IngestResponse)
def ingest_from_url(req: IngestUrlRequest):
    try:
        evidence = ingest_url(req.url.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch URL: {exc}") from exc
    colbert_store.index_evidence(evidence)
    return IngestResponse(filename=req.url, evidence=evidence, total=len(evidence))


@router.post("/ingest/text", response_model=IngestResponse)
def ingest_from_text(req: IngestTextRequest):
    evidence = ingest_text(req.text, req.title or "Pasted text")
    colbert_store.index_evidence(evidence)
    return IngestResponse(filename=req.title or "Pasted text", evidence=evidence, total=len(evidence))

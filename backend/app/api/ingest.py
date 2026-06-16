"""POST /api/ingest — Local file upload and text extraction."""
from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel
from ..domain.schemas import Evidence
from ..services.ingestion import ingest_file

router = APIRouter()


class IngestResponse(BaseModel):
    filename: str
    evidence: list[Evidence]
    total: int


@router.post("/ingest", response_model=IngestResponse)
async def ingest_document(file: UploadFile = File(...)):
    content = await file.read()
    evidence = ingest_file(file.filename or "upload", content)
    return IngestResponse(filename=file.filename or "upload", evidence=evidence, total=len(evidence))

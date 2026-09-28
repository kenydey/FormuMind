"""POST /api/ingest — Local file upload, URL fetch, pasted text, and batch upload."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..config import get_settings
from ..domain.schemas import Evidence, SourceGuideSchema
from ..services import colbert_store
from ..services.ingestion import ingest_text, ingest_url
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


async def _read_upload_capped(file: UploadFile, filename: str) -> bytes:
    """分块读取上传文件，累计字节数；超限即停并抛 413。

    B-17：此前先 ``await file.read()`` 全量读入内存再检查大小，大文件可致
    内存耗尽（DoS）。此处按 1MiB 分块读，一旦累计字节超过
    ``ingest_max_upload_bytes`` 立刻 413，不再继续读后续字节。
    """
    limit = get_settings().ingest_max_upload_bytes
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=413,
                detail=f"File {filename!r} exceeds upload limit ({limit // (1024 * 1024)} MiB)",
            )
        chunks.append(chunk)
    return b"".join(chunks)


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
    content = await _read_upload_capped(file, file.filename or "upload")
    filename = file.filename or "upload"

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
            name = f.filename or "upload"
            content = await _read_upload_capped(f, name)
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


@router.get("/sources/{source_id}/tables", response_model=dict)
def get_source_tables(source_id: str):
    """Table assets extracted from a source (W2-3 table contract; W3-7 UI).

    Passes through the raw sidecar payload so W3-1 PropertySet fields
    (``property_set``) survive without schema changes. Fail-open: a missing
    or corrupt sidecar yields an empty table list, never a 500.
    """
    from ..services import table_contract as _tc

    tables: list[dict] = []
    try:
        path = _tc._tables_path(source_id)
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            raw = payload.get("tables", [])
            if isinstance(raw, list):
                tables = [t for t in raw if isinstance(t, dict)]
    except Exception:
        logger.exception("get_source_tables failed (fail-open)")
    return {"source_id": source_id, "tables": tables}


# ── W3-5 (P1-36/37): paper detail aggregation + batch export ────────────────

_DETAIL_MAX_CHUNKS = 200
_DETAIL_CHUNK_TEXT_LIMIT = 4000
_EXPORT_MAX_SOURCES = 10


class DetailChunk(BaseModel):
    ord: int
    page_no: int | None = None
    heading_path: str = ""
    text: str = ""
    chars: int = 0


class DetailTableSummary(BaseModel):
    table_id: str
    page_no: int = 1
    caption: str = ""
    kind: str = "other"
    n_rows: int = 0
    n_cols: int = 0
    # W3-1 (P1-19) normalizer output, read raw from the sidecar when present.
    property_set: dict[str, Any] | None = None


class SourceDetailResponse(BaseModel):
    id: str
    filename: str
    title: str
    source_kind: str
    raw_text_chars: int
    extraction_status: str
    created_at: datetime
    has_fulltext: bool
    total_chunks: int
    chunks_truncated: bool
    chunks: list[DetailChunk]
    tables: list[DetailTableSummary]
    provenance_upstream: list[dict[str, Any]]


class SourceExportRequest(BaseModel):
    source_ids: list[str] = Field(default_factory=list)
    format: Literal["docx", "pdf", "html", "md"] = "docx"


def _detail_session_factory(store):
    """Indirection for test injection (fail-open path)."""
    return store._session_factory


def _read_detail_chunks(store, source_id: str) -> tuple[list[DetailChunk], int]:
    """Fail-open chunk read. Returns (chunks, total_count)."""
    try:
        from sqlalchemy import func, select

        from ..db.models import DocumentChunk

        sf = _detail_session_factory(store)
        with sf() as session:
            total = (
                session.execute(
                    select(func.count())
                    .select_from(DocumentChunk)
                    .where(DocumentChunk.source_id == source_id)
                ).scalar()
                or 0
            )
            rows = (
                session.execute(
                    select(DocumentChunk)
                    .where(DocumentChunk.source_id == source_id)
                    .order_by(DocumentChunk.ord)
                    .limit(_DETAIL_MAX_CHUNKS)
                )
                .scalars()
                .all()
            )
    except Exception:  # noqa: BLE001
        logger.warning("source detail: chunk read failed (fail-open)", exc_info=True)
        return [], 0
    chunks: list[DetailChunk] = []
    for r in rows:
        raw = r.text or ""
        text = (
            raw[:_DETAIL_CHUNK_TEXT_LIMIT] + "…"
            if len(raw) > _DETAIL_CHUNK_TEXT_LIMIT
            else raw
        )
        chunks.append(
            DetailChunk(
                ord=r.ord,
                page_no=r.page_no,
                heading_path=r.heading_path or "",
                text=text,
                chars=len(raw),
            )
        )
    return chunks, total


def _read_property_sets(source_id: str) -> dict[str, dict[str, Any]]:
    """Raw sidecar read for W3-1 (P1-19) PropertySet output. {} when absent."""
    try:
        import json

        from ..services import table_contract

        path = table_contract._tables_path(source_id)  # noqa: SLF001
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        out: dict[str, dict[str, Any]] = {}
        for ps in payload.get("property_sets", []) or []:
            if isinstance(ps, dict) and ps.get("table_id"):
                out[str(ps["table_id"])] = ps
        return out
    except Exception:  # noqa: BLE001
        logger.warning("source detail: property_set read failed (fail-open)", exc_info=True)
        return {}


def _read_detail_tables(source_id: str) -> list[DetailTableSummary]:
    try:
        from ..services import table_contract

        assets = table_contract.load_tables(source_id)
    except Exception:  # noqa: BLE001
        logger.warning("source detail: table read failed (fail-open)", exc_info=True)
        return []
    prop_sets = _read_property_sets(source_id)
    summaries: list[DetailTableSummary] = []
    for a in assets or []:
        try:
            summaries.append(
                DetailTableSummary(
                    table_id=a.table_id,
                    page_no=a.page_no,
                    caption=a.caption or "",
                    kind=a.kind or "other",
                    n_rows=len(a.rows or []),
                    n_cols=len(a.headers or []),
                    property_set=prop_sets.get(a.table_id),
                )
            )
        except Exception:  # noqa: BLE001
            logger.warning("source detail: bad table asset skipped", exc_info=True)
    return summaries


def _read_upstream_edges(source_id: str) -> list[dict[str, Any]]:
    try:
        from ..services import provenance

        edges = provenance.lineage("source", source_id, depth=3)
        return [dict(e) for e in (edges or [])]
    except Exception:  # noqa: BLE001
        logger.warning("source detail: lineage read failed (fail-open)", exc_info=True)
        return []


@router.get("/sources/{source_id}/detail", response_model=SourceDetailResponse)
def get_source_detail(source_id: str):
    """P1-36: one-call paper detail — metadata + chunks + tables + lineage."""
    row = get_source_store().get(source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Source not found")
    chunks, total = _read_detail_chunks(get_source_store(), source_id)
    return SourceDetailResponse(
        id=row.id,
        filename=row.filename,
        title=row.title,
        source_kind=row.source_kind,
        raw_text_chars=row.raw_text_chars,
        extraction_status=row.extraction_status,
        created_at=row.created_at,
        has_fulltext=bool(getattr(row, "full_text", None)),
        total_chunks=total,
        chunks_truncated=total > len(chunks),
        chunks=chunks,
        tables=_read_detail_tables(source_id),
        provenance_upstream=_read_upstream_edges(source_id),
    )


def _assemble_sources_markdown(docs, missing: list[str]) -> str:
    from ..services import table_contract

    lines = ["# 文献批量导出", ""]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    note = f"导出时间：{stamp}；共 {len(docs)} 篇"
    if missing:
        note += f"；{len(missing)} 篇未找到已跳过"
    lines.append(note)
    refs: list[str] = []
    for i, doc in enumerate(docs, 1):
        lines += ["", f"## {i}. {doc.title or doc.filename}", ""]
        lines.append(f"- 文件名：{doc.filename}")
        lines.append(f"- 类型：{doc.source_kind}")
        lines.append(f"- 入库时间：{doc.created_at}")
        lines.append(f"- 全文字符数：{doc.raw_text_chars}")
        guide = doc.source_guide if isinstance(doc.source_guide, dict) else {}
        summary = (guide.get("summary") or "").strip()
        if summary:
            lines += ["", "### 摘要", "", summary]
        entities = [str(e) for e in (guide.get("key_entities") or [])][:30]
        if entities:
            lines += ["", "### 关键实体", "", "、".join(entities)]
        try:
            tables = table_contract.load_tables(doc.id)
        except Exception:  # noqa: BLE001
            tables = []
        if tables:
            kinds: dict[str, int] = {}
            for t in tables:
                kinds[t.kind or "other"] = kinds.get(t.kind or "other", 0) + 1
            lines += [
                "",
                "### 表格",
                "",
                "共 %d 张：" % len(tables)
                + "、".join(f"{k}×{v}" for k, v in sorted(kinds.items())),
            ]
        refs.append(f"[{i}] {doc.title or doc.filename}（source_id={doc.id}）")
    lines += ["", "## 引用清单", ""]
    lines += [f"- {r}" for r in refs]
    return "\n".join(lines) + "\n"


@router.post("/sources/export")
def export_sources(body: SourceExportRequest) -> Response:
    """P1-37: export up to 10 sources as one docx/pdf/html/md file."""
    from ..services.wiki.report_export import export_bytes

    ids = [s.strip() for s in (body.source_ids or []) if s and s.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="source_ids 不能为空")
    if len(ids) > _EXPORT_MAX_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"一次最多导出 {_EXPORT_MAX_SOURCES} 篇",
        )
    store = get_source_store()
    docs, missing = [], []
    for sid in ids:
        row = store.get(sid)
        if row is None:
            missing.append(sid)
        else:
            docs.append(row)
    if not docs:
        raise HTTPException(status_code=404, detail="Source not found")
    markdown = _assemble_sources_markdown(docs, missing)
    try:
        payload, media_type, ext = export_bytes(
            markdown, body.format, title="文献批量导出"
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="sources_export.{ext}"'},
    )

"""Persistent knowledge-base endpoints (KB v2).

GET  /api/kb/stats    — corpus counters (sources, chunks, embeddings)
POST /api/kb/reindex  — rebuild chunk rows for every stored source
GET  /api/kb/search   — direct chunk retrieval (debug / power users)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from ..domain.schemas import ChunkListResponse, DocumentChunkResponse, Evidence
from ..services import kb_index

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kb", tags=["kb"])


class HybridSearchRequest(BaseModel):
    query: str
    top_k: int = 10
    alpha: float = 0.3

    @field_validator("alpha")
    @classmethod
    def _validate_alpha(cls, v: float) -> float:
        if v < 0.0 or v > 1.0:
            raise ValueError(f"alpha must be in [0, 1], got {v}")
        return v


class KBStats(BaseModel):
    enabled: bool
    sources: int
    sources_by_kind: dict[str, int]
    chunks: int
    embedded_chunks: int
    #: Import-only probe. Says the library is present, NOT that anything got
    #: embedded — see `vector_mode` for that.
    embedding_available: bool
    #: "semantic" | "degraded" | "keyword" | "empty" — whether retrieval really
    #: is vector-based. `degraded` is the dangerous one: library installed, zero
    #: vectors, looks healthy.
    vector_mode: str = "empty"
    vector_hint: str = ""
    #: The backend `build_store` would actually pick, not the configured value.
    rag_backend: str = "tfidf"
    products: int = 0


class ReindexResult(BaseModel):
    reindexed_sources: int
    reindexed_chunks: int
    total_chunks: int
    embedded_chunks: int


class KBSearchResponse(BaseModel):
    results: list[Evidence]


@router.get("/stats", response_model=KBStats)
def stats() -> KBStats:
    return KBStats(**kb_index.kb_stats())


@router.post("/reindex", response_model=ReindexResult)
def reindex(embed: bool = True) -> ReindexResult:
    if not kb_index.kb_enabled():
        raise HTTPException(status_code=409, detail="知识库 v2 未启用（FORMUMIND_KB_V2_ENABLED）")
    try:
        return ReindexResult(**kb_index.reindex_all(embed=embed))
    except Exception as exc:
        logger.exception("kb reindex failed")
        raise HTTPException(status_code=500, detail="操作失败") from exc


@router.get("/search", response_model=KBSearchResponse)
def search(
    q: str = Query(min_length=1),
    k: int = Query(default=6, ge=1, le=50),
    project_id: str | None = Query(default=None),
) -> KBSearchResponse:
    return KBSearchResponse(results=kb_index.search_chunks(q, k=k, project_id=project_id))


class KBSourceItem(BaseModel):
    id: str
    title: str
    filename: str
    source_kind: str
    origin_url: str | None = None
    project_id: str | None = None
    raw_text_chars: int = 0
    extraction_status: str = ""


class KBSourcesResponse(BaseModel):
    sources: list[KBSourceItem]
    total: int


@router.get("/sources", response_model=KBSourcesResponse)
def list_sources(
    project_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> KBSourcesResponse:
    from ..db.source_store import get_source_store

    rows = get_source_store().list_for_project(project_id, limit=limit)
    return KBSourcesResponse(
        sources=[
            KBSourceItem(
                id=r.id,
                title=r.title or r.filename,
                filename=r.filename,
                source_kind=r.source_kind,
                origin_url=r.origin_url,
                project_id=r.project_id,
                raw_text_chars=int(r.raw_text_chars or 0),
                extraction_status=r.extraction_status or "",
            )
            for r in rows
        ],
        total=len(rows),
    )


class KBProductItem(BaseModel):
    trade_name: str
    grade: str = ""
    supplier: str = ""
    generic_name: str = ""
    cas: str = ""
    smiles: str | None = None
    role: str = ""
    mention_count: int = 0
    sources: int = 0


class KBProductsResponse(BaseModel):
    products: list[KBProductItem]
    total: int


@router.get("/products", response_model=KBProductsResponse)
def products(
    q: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> KBProductsResponse:
    """Corpus-derived commercial product registry (牌号/供应商/通用名)."""
    from ..db.product_store import get_product_store

    store = get_product_store()
    rows = store.search(q, limit=limit, offset=offset)
    return KBProductsResponse(
        products=[
            KBProductItem(
                trade_name=r.trade_name,
                grade=r.grade or "",
                supplier=r.supplier or "",
                generic_name=r.generic_name or "",
                cas=r.cas or "",
                smiles=r.smiles,
                role=r.role or "",
                mention_count=int(r.mention_count or 0),
                sources=len(r.source_ids or []),
            )
            for r in rows
        ],
        total=store.count(),
    )


@router.get(
    "/chunks/by-source/{source_id}",
    response_model=ChunkListResponse,
    tags=["kb"],
)
def chunks_by_source(source_id: str) -> ChunkListResponse:
    """Retrieve all chunks for a source document with page/paragraph/offset info."""
    from ..db.chunk_store import get_chunk_store

    store = get_chunk_store()
    rows = store.get_by_source(source_id)
    return ChunkListResponse(
        chunks=[
            DocumentChunkResponse(
                id=row.id,
                source_id=row.source_id,
                ord=row.ord,
                text=row.text,
                heading_path=row.heading_path or "",
                page=row.page_no,
                paragraph=row.paragraph_idx
                if row.paragraph_idx is not None
                else (row.meta or {}).get("paragraph_idx"),
                offset_start=row.offset_start
                if row.offset_start is not None
                else (row.meta or {}).get("offset_start"),
                offset_end=row.offset_end
                if row.offset_end is not None
                else (row.meta or {}).get("offset_end"),
                meta=row.meta,
            )
            for row in rows
        ]
    )


# ── ingest ────────────────────────────────────────────────────────────────────


@router.post("/hybrid-search", response_model=list[DocumentChunkResponse])
def hybrid_search(body: HybridSearchRequest) -> list[DocumentChunkResponse]:
    """BM25 + vector hybrid retrieval over the persistent KB chunk store."""
    if not kb_index.kb_enabled():
        raise HTTPException(status_code=409, detail="知识库 v2 未启用")
    from ..services.hybrid_search import hybrid_search as _hs

    return _hs(body.query, top_k=body.top_k, alpha=body.alpha)


# ── ingest ────────────────────────────────────────────────────────────────────


class IngestRequest(BaseModel):
    text: str = Field(..., max_length=5_000_000)
    source_id: str | None = None
    title: str = ""
    metadata: dict | None = None


class IngestResponse(BaseModel):
    source_id: str
    chunk_count: int
    status: str


@router.post("/ingest", response_model=IngestResponse, summary="全文入库（幂等）")
def ingest(body: IngestRequest) -> IngestResponse:
    """Full-document ingest → chunk + index + store + outbox record.

    Idempotent: repeating the same ``source_id`` returns the same result
    without re-indexing.
    """
    import hashlib
    import uuid as _uuid

    from ..db.database import default_session_factory
    from ..db.models import SourceDocument
    from ..db.outbox_store import enqueue
    # NOTE: metadata parameter accepted for future expansion (Task 2.4).

    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    source_id = body.source_id or str(_uuid.uuid4())

    # Ensure a SourceDocument row exists for this source_id
    factory = default_session_factory()
    with factory() as session:
        doc = session.get(SourceDocument, source_id)
        if doc is not None and body.source_id is not None:
            # User-supplied source_id collides with an existing document.
            # Idempotent when content matches (return same result without
            # re-indexing); reject only when the content actually differs,
            # to prevent silent overwrite of another source's content.
            new_hash = hashlib.sha256(
                text.encode("utf-8", errors="replace")
            ).hexdigest()
            if (doc.content_hash or "") != new_hash:
                raise HTTPException(
                    status_code=409,
                    detail=f"source_id {source_id} already exists with different content",
                )
            # Same content → idempotent: skip indexing, return chunk_count=0
            return IngestResponse(
                source_id=source_id,
                chunk_count=0,
                status="ok",
            )
        if doc is None:
            session.add(
                SourceDocument(
                    id=source_id,
                    filename=body.title or "api_ingest",
                    title=body.title or "API Ingest",
                    source_kind="api",
                    full_text=text,
                    content_hash=hashlib.sha256(
                        text.encode("utf-8", errors="replace")
                    ).hexdigest(),
                    raw_text_chars=len(text),
                )
            )
            session.commit()

    chunk_count = kb_index.ingest_full_document(source_id, text, body.metadata)

    # Outbox record: idempotent (keyed on source_id)
    with factory() as session:
        enqueue(
            session,
            operation="ingest_complete",
            idempotency_key=source_id,
            payload={
                "source_id": source_id,
                "chunk_count": chunk_count,
                "status": "ok",
            },
        )
        session.commit()

    return IngestResponse(
        source_id=source_id,
        chunk_count=chunk_count,
        status="ok",
    )


class OrphanReportView(BaseModel):
    reference: str
    orphans: int = 0
    checked: int = 0
    healthy: bool = True
    unjoinable: bool = False
    note: str = ""


class IntegrityResponse(BaseModel):
    healthy: bool
    total_orphans: int
    external_backend: bool
    references: list[OrphanReportView] = Field(default_factory=list)


@router.get("/integrity", response_model=IntegrityResponse)
def integrity() -> IntegrityResponse:
    """Orphan scan over the references that carry no database constraint.

    Several of them cannot take one — under the Datalab backend the target row
    lives in an external ELN — so the alternative to a constraint that cannot
    be enforced is a check that is actually run.
    """
    from ..db.integrity import check_integrity

    report = check_integrity()
    return IntegrityResponse(
        healthy=report.healthy,
        total_orphans=report.total_orphans,
        external_backend=report.external_backend,
        references=[
            OrphanReportView(
                reference=r.reference, orphans=r.orphans, checked=r.checked,
                healthy=r.healthy, unjoinable=r.unjoinable, note=r.note,
            )
            for r in report.references
        ],
    )

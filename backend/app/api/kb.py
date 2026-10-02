"""Persistent knowledge-base endpoints (KB v2).

GET  /api/kb/stats    — corpus counters (sources, chunks, embeddings)
POST /api/kb/reindex  — rebuild chunk rows for every stored source
GET  /api/kb/search   — direct chunk retrieval (debug / power users)
"""
from __future__ import annotations

from typing import Literal

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from ..domain.schemas import ChunkListResponse, DocumentChunkResponse, Evidence
from ..services import kb_index

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kb", tags=["kb"])


class HybridSearchRequest(BaseModel):
    query: str
    # P2: top_k 加上限；alpha 默认 None → 走 kb_hybrid_alpha 设置项
    # （此前硬编码 0.3 覆盖设置）；补 project_id/include_global。
    top_k: int = Field(default=10, ge=1, le=50)
    alpha: float | None = Field(default=None, ge=0.0, le=1.0)
    project_id: str | None = None
    include_global: bool = True

    @field_validator("alpha")
    @classmethod
    def _validate_alpha(cls, v: float | None) -> float | None:
        if v is not None and not 0.0 <= v <= 1.0:
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
    #: Process-local quality-gate drop counters (retrieval + ingest).
    quality_gate_drops: dict[str, dict[str, int]] = Field(default_factory=dict)
    # W4 scan-pain / retention observability
    sources_active: int = 0
    sources_archived: int = 0
    chunks_active: int = 0
    chunks_archived: int = 0
    scan_limit: int = 5000
    scan_pressure: float = 0.0
    scan_near_cap: bool = False
    archive_retention_days: int = 0
    suppliers_json_dual_write: bool = True
    stale_chunks: int = 0
    products_pending_structure: int = 0
    # Option A: configurable embedding upgrade path (FORMUMIND_EMBEDDING_MODEL).
    embedding_model: str = ""
    embedding_model_configured: str | None = None
    embedding_catalog: list[dict[str, str]] = Field(default_factory=list)
    reindex_hint: str = ""


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


@router.get("/quality-ops")
def quality_ops(
    project_id: str | None = Query(default=None, description="Optional project scope label"),
) -> dict:
    """Batch D: Hub quality ops panel — read-only aggregate of stats + shadow + score."""
    from ..services.kb_quality_ops import build_quality_ops

    return build_quality_ops(project_id=project_id)

class RetentionPurgeRequest(BaseModel):
    """W4: purge soft-archived sources older than ``days``.

    Default is dry-run. Physical delete requires ``confirm=true`` and
    ``dry_run=false``. ``days`` must be ≥ 1 (config ``kb_archive_retention_days``
    is display-only and never auto-runs).
    """

    days: int = Field(..., ge=1, le=3650)
    confirm: bool = False
    dry_run: bool = True
    limit: int = Field(default=200, ge=1, le=1000)


class RetentionPurgeCandidate(BaseModel):
    source_id: str
    title: str | None = None
    archived_at: str | None = None


class RetentionPurgeResponse(BaseModel):
    ok: bool
    dry_run: bool
    days: int
    candidates: list[RetentionPurgeCandidate] = Field(default_factory=list)
    candidate_count: int = 0
    purged: list[str] = Field(default_factory=list)
    purged_count: int = 0
    errors: list[dict[str, str]] = Field(default_factory=list)


@router.post("/retention/purge", response_model=RetentionPurgeResponse)
def retention_purge(body: RetentionPurgeRequest) -> RetentionPurgeResponse:
    """Flag-gated hard-delete of expired soft-archived sources (default dry-run)."""
    if not kb_index.kb_enabled():
        raise HTTPException(status_code=409, detail="知识库 v2 未启用（FORMUMIND_KB_V2_ENABLED）")
    from ..services.kb_retention import purge_expired_archived

    try:
        result = purge_expired_archived(
            days=body.days,
            dry_run=body.dry_run,
            confirm=body.confirm,
            limit=body.limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("kb retention purge failed")
        raise HTTPException(status_code=500, detail="清理失败") from exc
    return RetentionPurgeResponse(
        ok=bool(result.get("ok")),
        dry_run=bool(result.get("dry_run")),
        days=int(result.get("days") or body.days),
        candidates=[
            RetentionPurgeCandidate(**c) for c in (result.get("candidates") or [])
        ],
        candidate_count=int(result.get("candidate_count") or 0),
        purged=list(result.get("purged") or []),
        purged_count=int(result.get("purged_count") or 0),
        errors=list(result.get("errors") or []),
    )


class KbRetrievalSettings(BaseModel):
    """Shared probe ↔ recommend retrieval knobs (read-only snapshot)."""

    kb_hybrid_alpha: float = 0.3
    kb_recommend_use_hybrid: bool = True
    kb_recommend_include_global: bool = True
    kb_recommend_top_k: int = 4
    kb_recommend_rerank_enabled: bool = False


@router.get("/retrieval-settings", response_model=KbRetrievalSettings)
def retrieval_settings() -> KbRetrievalSettings:
    """Defaults shared by Hub retrieval probe and recommend hybrid fuse."""
    from ..config import get_settings

    s = get_settings()
    return KbRetrievalSettings(
        kb_hybrid_alpha=float(getattr(s, "kb_hybrid_alpha", 0.3)),
        kb_recommend_use_hybrid=bool(getattr(s, "kb_recommend_use_hybrid", True)),
        kb_recommend_include_global=bool(getattr(s, "kb_recommend_include_global", True)),
        kb_recommend_top_k=int(getattr(s, "kb_recommend_top_k", 4) or 0),
        kb_recommend_rerank_enabled=bool(getattr(s, "kb_recommend_rerank_enabled", False)),
    )


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
    langs: str | None = Query(
        default=None,
        description="逗号分隔的语言过滤（如 zh,en）；不传则走统一 hybrid 门面",
    ),
) -> KBSearchResponse:
    # P1-9: 切统一检索门面 —— 真 BM25+向量融合 / Faiss / children /
    # 统一 alpha 与 rerank 开关；此前直连 legacy search_chunks 受
    # kb_search_scan_limit=5000 与 Python 全扫描限制。
    lang_list = [s.strip() for s in langs.split(",") if s.strip()] if langs else None
    return KBSearchResponse(
        results=kb_index.retrieve_evidence(
            q,
            k=k,
            project_id=project_id,
            include_global=False,
            langs=lang_list,
        )
    )


class KBSourceItem(BaseModel):
    id: str
    title: str
    filename: str
    source_kind: str
    origin_url: str | None = None
    project_id: str | None = None
    raw_text_chars: int = 0
    extraction_status: str = ""
    archived: bool = False


class KBSourcesResponse(BaseModel):
    sources: list[KBSourceItem]
    total: int


@router.get("/sources", response_model=KBSourcesResponse)
def list_sources(
    project_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    include_global: bool = Query(
        default=False,
        description="When project_id is set, also include global (project_id NULL) sources",
    ),
    include_archived: bool = Query(
        default=False,
        description="Include soft-archived sources (default: hide)",
    ),
) -> KBSourcesResponse:
    from ..db.source_store import get_source_store

    rows = get_source_store().list_for_project(
        project_id,
        limit=limit,
        include_global=include_global,
        include_archived=include_archived,
    )
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
                archived=bool(getattr(r, "archived", False)),
            )
            for r in rows
        ],
        total=len(rows),
    )


class KBExtractionTableItem(BaseModel):
    id: str
    page_no: int | None = None
    bbox: list | None = None
    caption: str | None = None
    markdown_text: str = ""
    n_rows: int | None = None
    n_cols: int | None = None


class KBExtractionFormulaItem(BaseModel):
    id: str
    page_no: int | None = None
    bbox: list | None = None
    latex: str = ""
    formula_no: str | None = None


@router.get("/sources/{source_id}/tables", response_model=list[KBExtractionTableItem])
def source_tables(source_id: str):
    """Up-5A: 该来源抽取的表格（MinerU 结构化解析写入；bbox 待真实样本验证）。"""
    from ..db.database import default_session_factory
    from ..db.extraction_store import ExtractionStore

    rows = ExtractionStore(default_session_factory()).tables_for_source(source_id)
    return [
        KBExtractionTableItem(
            id=r.id,
            page_no=r.page_no,
            bbox=r.bbox,
            caption=r.caption,
            markdown_text=r.markdown_text or "",
            n_rows=r.n_rows,
            n_cols=r.n_cols,
        )
        for r in rows
    ]


@router.get("/sources/{source_id}/formulas", response_model=list[KBExtractionFormulaItem])
def source_formulas(source_id: str):
    """Up-5A: 该来源抽取的公式（MinerU MFR 写入；bbox 待真实样本验证）。"""
    from ..db.database import default_session_factory
    from ..db.extraction_store import ExtractionStore

    rows = ExtractionStore(default_session_factory()).formulas_for_source(source_id)
    return [
        KBExtractionFormulaItem(
            id=r.id,
            page_no=r.page_no,
            bbox=r.bbox,
            latex=r.latex or "",
            formula_no=r.formula_no,
        )
        for r in rows
    ]


class KBSourceArchiveRequest(BaseModel):
    archived: bool = True


class KBSourceArchiveResponse(BaseModel):
    ok: bool
    source_id: str
    archived: bool


@router.post(
    "/sources/{source_id}/archive",
    response_model=KBSourceArchiveResponse,
    summary="软归档 / 恢复知识库文档（保留切块，检索默认排除）",
)
def archive_source(source_id: str, body: KBSourceArchiveRequest) -> KBSourceArchiveResponse:
    """W3 soft-archive: hide from lists + retrieval without cascade delete."""
    if not kb_index.kb_enabled():
        raise HTTPException(status_code=409, detail="知识库 v2 未启用（FORMUMIND_KB_V2_ENABLED）")
    from ..db.source_store import get_source_store

    store = get_source_store()
    if store.get(source_id) is None:
        raise HTTPException(status_code=404, detail="source not found")
    if not store.set_archived(source_id, body.archived):
        raise HTTPException(status_code=500, detail="归档失败")
    return KBSourceArchiveResponse(
        ok=True, source_id=source_id, archived=bool(body.archived)
    )


class KBSourceDeleteResponse(BaseModel):
    ok: bool
    source_id: str
    chunks_removed: int = 0
    mentions_removed: int = 0
    links_removed: int = 0
    wiki_pages_touched: int = 0


@router.delete(
    "/sources/{source_id}",
    response_model=KBSourceDeleteResponse,
    summary="永久删除知识库文档（级联切块/KG/Wiki 引用）",
)
def delete_source(source_id: str) -> KBSourceDeleteResponse:
    """Knowledge Hub H2: hard-delete a persisted SourceDocument."""
    if not kb_index.kb_enabled():
        raise HTTPException(status_code=409, detail="知识库 v2 未启用（FORMUMIND_KB_V2_ENABLED）")
    from ..services.kb_delete import delete_kb_source

    try:
        result = delete_kb_source(source_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="source not found") from None
    except Exception as exc:
        logger.exception("kb source delete failed")
        raise HTTPException(status_code=500, detail="删除失败") from exc
    return KBSourceDeleteResponse(**result)


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


@router.get("/products", response_model=KBProductsResponse, include_in_schema=False)
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
def chunks_by_source(
    source_id: str,
    limit: int = Query(default=200, ge=1, le=2000, description="每页返回的 chunk 数"),
    offset: int = Query(default=0, ge=0, description="跳过前 N 个 chunk"),
) -> ChunkListResponse:
    """Retrieve chunks for a source document with page/paragraph/offset info.

    P-6: paginated — previously the endpoint returned every chunk of the
    source in a single response. The default limit keeps existing callers
    working (first page); pass explicit limit/offset for the rest.
    """
    from ..db.chunk_store import get_chunk_store

    store = get_chunk_store()
    rows = store.get_by_source(source_id, limit=limit, offset=offset)
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

    return _hs(
        body.query,
        top_k=body.top_k,
        alpha=body.alpha,
        project_id=body.project_id,
        include_global=body.include_global,
    )


# ── retrieval probe (query-test) ─────────────────────────────────────────────


class QueryTestRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    mode: Literal["keyword", "hybrid", "hybrid_rerank"] = "hybrid"
    top_k: int = Field(default=10, ge=1, le=50)
    alpha: float = Field(default=0.3, ge=0.0, le=1.0)
    project_id: str | None = None
    include_global: bool = True
    rerank: bool | None = None


class QueryTestHit(BaseModel):
    rank: int
    chunk_id: str | None = None
    source_id: str | None = None
    ord: int | None = None
    title: str = ""
    snippet: str = ""
    bm25_score: float | None = None
    cosine_score: float | None = None
    hybrid_score: float | None = None
    relevance: float | None = None
    rerank_score: float | None = None
    rank_before_rerank: int | None = None
    meta: dict | None = None


class QueryTestResponse(BaseModel):
    query: str
    mode: str
    params: dict
    vector_mode: str = "empty"
    elapsed_ms: int = 0
    hits: list[QueryTestHit] = Field(default_factory=list)
    warning: str | None = None
    #: Drops during this probe run (hybrid path only; keyword → zeros).
    gate_drops: dict[str, dict[str, int]] = Field(default_factory=dict)
    #: Process-lifetime counters at response time.
    gate_drops_total: dict[str, dict[str, int]] = Field(default_factory=dict)


class GoldenEvalRequest(BaseModel):
    mode: Literal["keyword", "hybrid", "hybrid_rerank"] = "hybrid"
    top_k: int = Field(default=3, ge=1, le=20)
    alpha: float = Field(default=0.3, ge=0.0, le=1.0)
    project_id: str | None = None
    include_global: bool = True
    rerank: bool | None = None


class GoldenQuestionItem(BaseModel):
    question: str
    expected_keywords: list[str] = Field(default_factory=list)
    category: str = ""


@router.post("/query-test", response_model=QueryTestResponse)
def query_test(body: QueryTestRequest) -> QueryTestResponse:
    """Scored KB retrieval probe for the Knowledge Hub workbench."""
    if not kb_index.kb_enabled():
        raise HTTPException(status_code=409, detail="知识库 v2 未启用")
    from ..services.kb_query_test import run_query_test

    try:
        payload = run_query_test(
            query=body.query,
            mode=body.mode,
            top_k=body.top_k,
            alpha=body.alpha,
            project_id=body.project_id,
            include_global=body.include_global,
            rerank=body.rerank,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return QueryTestResponse(**payload)


@router.get("/golden-questions", response_model=list[GoldenQuestionItem])
def golden_questions() -> list[GoldenQuestionItem]:
    """List curated golden retrieval questions (for Hub batch runner)."""
    from ..services.kb_query_test import list_golden_questions

    return [GoldenQuestionItem(**row) for row in list_golden_questions()]


@router.post("/golden-eval/run")
def golden_eval_run(body: GoldenEvalRequest) -> dict:
    """Run golden questions through query-test; keyword-hit@top_k + MRR/Recall@k."""
    if not kb_index.kb_enabled():
        raise HTTPException(status_code=409, detail="知识库 v2 未启用")
    from ..services.kb_query_test import run_golden_eval

    try:
        return run_golden_eval(
            mode=body.mode,
            top_k=body.top_k,
            alpha=body.alpha,
            project_id=body.project_id,
            include_global=body.include_global,
            rerank=body.rerank,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/relevance-shadow/stats")
def relevance_shadow_stats(
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    """Aggregate topicality shadow batches (calibration — enforce is separate).

    Use reject rates + shadow_only/topic_only/both overlap to decide when to
    flip ``kb_relevance_shadow`` off (W2 enforce). Kill-switch: set the flag
    back to True.
    """
    from ..services.kb_ingest_audit import load_relevance_shadow_stats

    return load_relevance_shadow_stats(limit=limit)


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


class IngestEvidenceRequest(BaseModel):
    """P3.2 — one-click fulltext ingest from a search Evidence row."""

    identifier: str = Field(..., min_length=1, max_length=1024)
    title: str | None = None
    url: str | None = None
    url_alt: str | None = None
    source: str | None = None
    project_id: str | None = None
    assignee: str | None = None
    pub_date: str | None = None
    snippet: str | None = None
    oa_pdf_url: str | None = None
    is_oa: bool | None = None
    relevance: float = Field(default=0.9, ge=0, le=1)


class IngestEvidenceResponse(BaseModel):
    ok: bool
    status: str  # indexed | skipped | queued | failed
    source_id: str | None = None
    task_id: str | None = None
    status_url: str | None = None
    canonical_id: str | None = None
    reason: str | None = None
    kind: str | None = None


@router.post(
    "/ingest-evidence",
    response_model=IngestEvidenceResponse,
    summary="Evidence 一键入库全文（P3.2）",
)
def ingest_evidence(body: IngestEvidenceRequest) -> IngestEvidenceResponse:
    """Fetch + persist fulltext for one Evidence row (user-clicked).

    Independent of 「入库图谱」. Does **not** confirm embodiment drafts or
    write the production formula pool. Idempotent via origin_url aliases.
    """
    from ..services import kb_ingest

    if not kb_index.kb_enabled():
        raise HTTPException(status_code=409, detail="知识库 v2 未启用（FORMUMIND_KB_V2_ENABLED）")

    ev = Evidence(
        source=(body.source or "patent").strip() or "patent",
        identifier=body.identifier.strip(),
        title=(body.title or body.identifier).strip()[:500],
        snippet=(body.snippet or "").strip()[:2000],
        relevance=body.relevance,
        url=body.url,
        url_alt=body.url_alt,
        assignee=body.assignee,
        pub_date=body.pub_date,
        oa_pdf_url=body.oa_pdf_url,
        is_oa=body.is_oa,
    )
    result = kb_ingest.ingest_single_evidence(ev, project_id=body.project_id)
    return IngestEvidenceResponse(**result)


@router.post("/ingest", response_model=IngestResponse, summary="全文入库（幂等）", include_in_schema=False)
def ingest(body: IngestRequest) -> IngestResponse:
    """Full-document ingest → chunk + index + store + outbox record.

    Idempotent: repeating the same ``source_id`` returns the same result
    without re-indexing.

    P1-7: 经原子事务 ingest_document_tx（SourceDocument + chunks + outbox
    一次提交）—— 索引失败不再留下零 chunk 孤儿行。
    """
    import hashlib
    import uuid as _uuid

    from ..db.database import default_session_factory
    from ..db.models import SourceDocument
    from ..services.ingest_tx import ingest_document_tx
    # NOTE: metadata parameter accepted for future expansion (Task 2.4).

    source_id = body.source_id or str(_uuid.uuid4())
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    factory = default_session_factory()
    # 用户显式 source_id 撞库检查：内容相同 → 幂等返回；内容不同 → 409。
    with factory() as session:
        doc = session.get(SourceDocument, source_id)
        if doc is not None and body.source_id is not None:
            new_hash = hashlib.sha256(
                text.encode("utf-8", errors="replace")
            ).hexdigest()
            if (doc.content_hash or "") != new_hash:
                raise HTTPException(
                    status_code=409,
                    detail=f"source_id {source_id} already exists with different content",
                )
            return IngestResponse(
                source_id=source_id,
                chunk_count=0,
                status="ok",
            )

    try:
        result = ingest_document_tx(
            factory,
            source_id=source_id,
            text=text,
            title=body.title or "",
            metadata=body.metadata,
        )
    except Exception as exc:
        logger.exception("kb ingest tx failed")
        raise HTTPException(status_code=500, detail="入库失败") from exc

    return IngestResponse(
        source_id=result.source_id,
        chunk_count=result.chunk_count,
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

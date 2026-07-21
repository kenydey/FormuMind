"""SQLAlchemy ORM models for persisted platform state.

Currently the single source of truth is the experiment dataset: measured
DOE/lab results that train the data-driven predictors. Composite payloads
(``factors``, ``measured``) are stored as JSON columns, which keeps the schema
stable as new metrics appear without migrations.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_source_guide_type = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ExperimentRow(Base):
    """One measured DOE/lab result fed back into the platform.

    When ``item_id`` is set the payload lives in Datalab (``formumind_training`` block);
    otherwise factors/measured JSON columns hold the full record (sqlite fallback).
    """

    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    domain: Mapped[str] = mapped_column(String(64), index=True)
    project_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    factors: Mapped[dict] = mapped_column(JSON, default=dict)
    cure_temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    measured: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(64), default="lab")
    label: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Campaign(Base):
    """One AI optimization campaign (BayBE / active-learning round)."""

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    strategy: Mapped[str] = mapped_column(String(64), default="BayBE-LHS")
    status: Mapped[str] = mapped_column(String(32), default="IN_PROGRESS")
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    primary_metric: Mapped[str | None] = mapped_column(String(64), nullable=True)
    objectives_snapshot: Mapped[list | None] = mapped_column(JSON, nullable=True)
    lever_snapshot: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
    # Ordered Datalab sample refs: [{"id": 1, "item_id": "fm_c1_r1"}, ...]
    sample_refs: Mapped[list] = mapped_column(JSON, default=list)
    # Closed-loop round snapshots: [{round, at, rmse_by_metric, converged, ...}]
    loop_history: Mapped[list] = mapped_column(JSON, default=list)


class SourceDocument(Base):
    """Ingested source with full text and LLM-extracted source guide."""

    __tablename__ = "source_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(512), default="")
    title: Mapped[str] = mapped_column(String(512), default="")
    source_kind: Mapped[str] = mapped_column(String(32), default="local")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    # Provenance: the URL / patent number / DOI the document was fetched from.
    # Doubles as the async-ingest dedup key (don't re-download what we have).
    origin_url: Mapped[str | None] = mapped_column(String(1024), nullable=True, index=True)
    # Optional project scope — NULL = global corpus shared across projects.
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    full_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_text_chars: Mapped[int] = mapped_column(Integer, default=0)
    source_guide: Mapped[dict | None] = mapped_column(
        _source_guide_type, nullable=True, comment="LLM 提取的全局参数空间与摘要"
    )
    extraction_status: Mapped[str] = mapped_column(String(32), default="pending")
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class DocumentChunk(Base):
    """Persistent KB chunk — one structure-aware chunk of a SourceDocument.

    ``embedding`` (normalized vector, JSON list) is filled when
    sentence-transformers is installed; text-only rows still serve keyword
    retrieval, and ``reindex`` can backfill vectors later.
    """

    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(36), index=True)
    ord: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    heading_path: Mapped[str] = mapped_column(String(120), default="")
    # Source-page provenance (from <!-- page:N --> parser markers); citations
    # can point at the exact page of the original PDF.
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding: Mapped[list | None] = mapped_column(
        # none_as_null: Python None must become SQL NULL (not JSON 'null'),
        # so the embedded-rows count can filter with IS NOT NULL.
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"),
        nullable=True,
        comment="归一化句向量（JSON 数组）",
    )
    embedding_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Extracted entities: {"chem": [{type, value, ...}], "products": [...]}.
    meta: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"),
        nullable=True,
        comment="化学/产品实体元数据",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class KBProduct(Base):
    """Corpus-level registry of commercial chemical products (trade names).

    Aggregated across every ingested document: rule-tier chunk extraction and
    the LLM source-guide products both upsert here.  Feeds retrieval expansion
    (牌号 ↔ 通用名 ↔ CAS) and recommendation grounding.
    """

    __tablename__ = "kb_products"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # Normalized "trade|grade" key for idempotent upserts.
    norm_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    trade_name: Mapped[str] = mapped_column(String(120), default="")
    grade: Mapped[str] = mapped_column(String(60), default="")
    supplier: Mapped[str] = mapped_column(String(120), default="")
    generic_name: Mapped[str] = mapped_column(String(200), default="")
    cas: Mapped[str] = mapped_column(String(32), default="")
    smiles: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String(60), default="")
    mention_count: Mapped[int] = mapped_column(Integer, default=1)
    source_ids: Mapped[list] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=list
    )
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class KGEntity(Base):
    """Normalized knowledge-graph entity (chemical, trade product, element)."""

    __tablename__ = "kb_entities"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    canonical_name: Mapped[str] = mapped_column(String(512), default="")
    zh_name: Mapped[str] = mapped_column(String(256), default="")
    cas_no: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    smiles: Mapped[str | None] = mapped_column(Text, nullable=True)
    formula: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str] = mapped_column(String(64), default="")
    supplier: Mapped[str] = mapped_column(String(120), default="")
    grade: Mapped[str] = mapped_column(String(60), default="")
    composition_status: Mapped[str] = mapped_column(String(32), default="unknown")
    proprietary: Mapped[bool] = mapped_column(default=False)
    generic_name_hint: Mapped[str] = mapped_column(String(256), default="")
    linked_catalog_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    linked_product_key: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    element_symbols: Mapped[list] = mapped_column(JSON, default=list)
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    mention_count: Mapped[int] = mapped_column(Integer, default=0)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class KGMention(Base):
    """Entity occurrence in a document chunk."""

    __tablename__ = "kb_mentions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    source_id: Mapped[str] = mapped_column(String(36), index=True)
    chunk_id: Mapped[str] = mapped_column(String(36), index=True)
    surface_form: Mapped[str] = mapped_column(String(256), default="")
    char_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    extractor: Mapped[str] = mapped_column(String(32), default="chem_extract")
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class KGEntityLink(Base):
    """Optional link between entities (e.g. trade name → catalog chemical, semantic relations)."""

    __tablename__ = "kb_entity_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    src_entity_id: Mapped[str] = mapped_column(String(64), index=True)
    dst_entity_id: Mapped[str] = mapped_column(String(64), index=True)
    link_type: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    is_valid: Mapped[bool] = mapped_column(default=True)
    extraction_method: Mapped[str] = mapped_column(String(16), default="rule")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        Index("idx_kb_link_triplet", "src_entity_id", "dst_entity_id", "link_type"),
    )


class ProjectRow(Base):
    """NotebookLM-style project workspace (JSON payload)."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    headline: Mapped[str] = mapped_column(String(512), default="")
    domain: Mapped[str] = mapped_column(String(64), index=True, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    is_archived: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

"""SQLAlchemy ORM models for persisted platform state.

Currently the single source of truth is the experiment dataset: measured
DOE/lab results that train the data-driven predictors. Composite payloads
(``factors``, ``measured``) are stored as JSON columns, which keeps the schema
stable as new metrics appear without migrations.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
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


class SourceDocument(Base):
    """Ingested source with full text and LLM-extracted source guide."""

    __tablename__ = "source_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(512), default="")
    title: Mapped[str] = mapped_column(String(512), default="")
    source_kind: Mapped[str] = mapped_column(String(32), default="local")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
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
    embedding: Mapped[list | None] = mapped_column(
        # none_as_null: Python None must become SQL NULL (not JSON 'null'),
        # so the embedded-rows count can filter with IS NOT NULL.
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"),
        nullable=True,
        comment="归一化句向量（JSON 数组）",
    )
    embedding_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


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

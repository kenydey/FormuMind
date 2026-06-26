"""SQLAlchemy ORM models for persisted platform state.

Currently the single source of truth is the experiment dataset: measured
DOE/lab results that train the data-driven predictors. Composite payloads
(``factors``, ``measured``) are stored as JSON columns, which keeps the schema
stable as new metrics appear without migrations.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

_source_guide_type = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ExperimentRow(Base):
    """One measured DOE/lab result fed back into the platform."""

    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(64), index=True)
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

    records: Mapped[list["ExperimentRecord"]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
        order_by="ExperimentRecord.id",
    )


class ExperimentRecord(Base):
    """Single lab execution row for the AG Grid workbench (not training registry rows)."""

    __tablename__ = "experiment_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="Pending")
    planned_params: Mapped[dict] = mapped_column(JSON, nullable=False)
    actual_params: Mapped[dict] = mapped_column(JSON, default=dict)
    measurements: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    campaign: Mapped["Campaign"] = relationship(back_populates="records")


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

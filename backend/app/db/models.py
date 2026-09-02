"""SQLAlchemy ORM models for persisted platform state.

Currently the single source of truth is the experiment dataset: measured
DOE/lab results that train the data-driven predictors. Composite payloads
(``factors``, ``measured``) are stored as JSON columns, which keeps the schema
stable as new metrics appear without migrations.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_source_guide_type = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
    owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, default=None)
    factors: Mapped[dict] = mapped_column(JSON, default=dict)
    cure_temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    measured: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(64), default="lab")
    label: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class MeasurementRow(Base):
    """One typed lab result belonging to an experiment.

    The experiment's ``measured`` JSON column stays as the flat mirror every
    consumer already reads; this table is where the context that makes a number
    comparable lives — unit, test method, instrument, operator, and the
    acceptance window it was judged against.

    Carries real foreign keys, unlike the rest of this schema: both parents are
    local tables (never Datalab-authoritative), so the constraint can be
    enforced rather than merely documented.
    """

    __tablename__ = "measurements"
    __table_args__ = (
        Index("ix_measurements_experiment_metric", "experiment_id", "metric"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), index=True
    )
    metric: Mapped[str] = mapped_column(String(80), index=True)
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32), default="")
    # ASTM B117 / ISO 9227 / GB/T 1771 — salt-spray hours are not comparable
    # across standards, so a value without one is not a result.
    test_method: Mapped[str] = mapped_column(String(80), default="")
    instrument: Mapped[str] = mapped_column(String(120), default="")
    operator: Mapped[str] = mapped_column(String(80), default="")
    measured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    spec_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    spec_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    passed: Mapped[bool | None] = mapped_column(nullable=True)
    # The QC report this value was read out of. SET NULL rather than CASCADE:
    # losing the report should not silently delete the measurement.
    source_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ExperimentAttachment(Base):
    """Binds an ingested document to the experiment it reports on.

    This link did not exist. QC reports were ingested into the knowledge base
    as generic corpus documents with no way back to the run they measured, so
    "show me the salt-spray certificate for this batch" had no answer.
    """

    __tablename__ = "experiment_attachments"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id", "source_document_id", name="uq_experiment_attachment"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), index=True
    )
    source_document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), default="qc_report")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class FormulationVersion(Base):
    """An immutable snapshot of a formulation, linked to what it came from.

    Formulation development is version-controlled work — a recipe is revised
    dozens of times and the interesting question is almost never "what is it
    now" but "what changed between v3 and v4, and why". Nothing recorded that:
    formulations lived in a project's JSON payload and were overwritten in
    place, so the reasoning behind every revision was lost as soon as the next
    one was saved.

    ``lineage_id`` groups the versions of one formulation; ``parent_version_id``
    records which revision this one was derived from, so branching (two people
    exploring different directions from the same v3) stays representable rather
    than being flattened into a single line.
    """

    __tablename__ = "formulation_versions"
    __table_args__ = (
        UniqueConstraint("lineage_id", "version", name="uq_formulation_version"),
        Index("ix_formulation_versions_lineage_version", "lineage_id", "version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    lineage_id: Mapped[str] = mapped_column(String(36), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    # Self-referential: NULL marks the root of a lineage. SET NULL rather than
    # CASCADE — deleting one revision must not silently erase its descendants.
    parent_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("formulation_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    domain: Mapped[str] = mapped_column(String(64), index=True)
    # Frozen Formulation dump. Immutable by contract: a revision is a new row,
    # never an edit, or the history stops being a history.
    snapshot: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=dict
    )
    change_summary: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Campaign(Base):
    """One AI optimization campaign (BayBE / active-learning round)."""

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    strategy: Mapped[str] = mapped_column(String(64), default="BayBE-LHS")
    status: Mapped[str] = mapped_column(String(32), default="IN_PROGRESS")
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, default=None)
    primary_metric: Mapped[str | None] = mapped_column(String(64), nullable=True)
    objectives_snapshot: Mapped[list | None] = mapped_column(JSON, nullable=True)
    lever_snapshot: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
    # Ordered Datalab sample refs: [{"id": 1, "item_id": "fm_c1_r1"}, ...]
    sample_refs: Mapped[list] = mapped_column(JSON, default=list)
    # P1: DataLab collection_id holding this campaign's DOE-row items
    # (collection_id in Datalab: "formumind_campaign_{id}")
    datalab_collection_id: Mapped[str | None] = mapped_column(
        String(96), nullable=True, default=None
    )
    # Closed-loop round snapshots: [{round, at, rmse_by_metric, converged, ...}]
    loop_history: Mapped[list] = mapped_column(JSON, default=list)

    __table_args__ = (
        CheckConstraint(
            "status IN ('IN_PROGRESS', 'COMPLETED', 'FAILED', 'ABORTED')",
            name="ck_campaign_status",
        ),
    )


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
    # Mirrors migration 0010. Declared here too so create_all() databases
    # (dev/test) carry the same invariant as migrated ones — ingest_tx relies
    # on the IntegrityError to detect a concurrent re-ingest of one source.
    __table_args__ = (
        UniqueConstraint("source_id", "ord", name="uq_document_chunks_source_ord"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(36), index=True)
    ord: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    heading_path: Mapped[str] = mapped_column(String(120), default="")
    # Source-page provenance (from <!-- page:N --> parser markers); citations
    # can point at the exact page of the original PDF.
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Anchor columns: promoted from meta JSON for efficient SQL range/grouping queries.
    # offset_start/end give the raw byte range of this chunk's text in source order;
    # paragraph_idx orders multi-paragraph sections.
    offset_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    offset_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paragraph_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
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


class MaterialRow(Base):
    """A raw material available to formulation search.

    Persists the curated ``knowledge.RAW_MATERIALS`` catalog and everything
    added on top of it at runtime — manual entries and trade products promoted
    out of ``kb_products``. Making the catalog data rather than a module literal
    is what lets ingredient *choice* become a search variable: inverse design
    and substitution both need a candidate pool that can grow.

    ``origin`` records provenance (``seed`` / ``user`` / ``kb_promoted``) so the
    curated 32 can always be told apart from harvested ones.

    NULL columns are omitted when converting back to a spec dict — absence is
    semantically distinct from None for several consumers (e.g. ``carrier``
    defaults to "both" via ``.get(k, default)``, which a stored None would break).
    """

    __tablename__ = "materials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # Normalized name, for idempotent upserts and case-insensitive lookup.
    norm_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    # Display name — the catalog key callers index RAW_MATERIALS by.
    name: Mapped[str] = mapped_column(String(200), index=True)
    role: Mapped[str] = mapped_column(String(60), default="")
    origin: Mapped[str] = mapped_column(String(16), default="seed", index=True)

    # ── curated chemistry (mirrors the seed literal) ──
    formula: Mapped[str | None] = mapped_column(String(120), nullable=True)
    smiles: Mapped[str | None] = mapped_column(Text, nullable=True)
    cas_no: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    zh_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    molar_mass: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_cny_per_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    voc_contrib: Mapped[float | None] = mapped_column(Float, nullable=True)
    density_gcm3: Mapped[float | None] = mapped_column(Float, nullable=True)
    oil_absorption: Mapped[float | None] = mapped_column(Float, nullable=True)
    tg_k: Mapped[float | None] = mapped_column(Float, nullable=True)
    lab: Mapped[list | None] = mapped_column(JSON, nullable=True)
    svhc: Mapped[bool | None] = mapped_column(nullable=True)
    carrier: Mapped[str | None] = mapped_column(String(16), nullable=True)
    water_compatible: Mapped[bool | None] = mapped_column(nullable=True)

    # ── substitution / sourcing metadata (new in the material space) ──
    # Chemical family within a role ("epoxy", "isocyanate", "amine", "phosphate",
    # "silane"…) — the first filter when looking for a drop-in replacement.
    functional_class: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    # Epoxy equivalent weight / amine value: needed to re-balance a swap.
    equivalent_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Hansen solubility parameters (MPa^0.5) — compatibility distance.
    hansen_d: Mapped[float | None] = mapped_column(Float, nullable=True)
    hansen_p: Mapped[float | None] = mapped_column(Float, nullable=True)
    hansen_h: Mapped[float | None] = mapped_column(Float, nullable=True)
    hlb: Mapped[float | None] = mapped_column(Float, nullable=True)
    supplier: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # in_stock | restricted | discontinued — drives supply-disruption alerts.
    availability: Mapped[str] = mapped_column(String(16), default="in_stock", index=True)
    regulatory: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Hand-tagged interchangeable group; members are drop-in for one another.
    substitute_group: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


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

    __table_args__ = (
        UniqueConstraint("entity_id", "chunk_id", "surface_form", name="uq_kb_mention_triple"),
    )


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
        UniqueConstraint("src_entity_id", "dst_entity_id", "link_type", name="uq_kb_link_triplet"),
        # v7: KG 图谱查询热点是 (src,dst)×link_type 复合过滤，单列索引下需回表过滤。
        Index("ix_kb_link_src_type", "src_entity_id", "link_type"),
        Index("ix_kb_link_dst_type", "dst_entity_id", "link_type"),
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


class TaskOutbox(Base):
    """Durable outbox row for async task dispatch (idempotency foundation).

    One row per logical task submission. ``(operation, idempotency_key)`` is
    unique so retried submissions collapse onto the same row instead of
    enqueueing duplicate work; the dispatcher claims PENDING rows FIFO by
    ``created_at`` and flips ``status``/``claimed_by``/``attempt_count`` as it
    processes them.
    """

    __tablename__ = "task_outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, default=None)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("operation", "idempotency_key", name="uq_task_outbox_op_key"),
        Index("ix_task_outbox_status_created", "status", "created_at"),
        Index("ix_task_outbox_claim", "claimed_by", "claimed_at"),
    )


class DOEPlanRow(Base):
    """Persisted DOE experimental design — one row per plan so recommendations
    from /baybe/recommend, /doe, and /doe/active are recorded idempotently
    for audit / reconciliation.
    """

    __tablename__ = "doe_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # Integer, matching the autoincrement primary keys these point at. They
    # were declared String(36), so neither column could ever join its parent —
    # a plan's experiment and campaign were unreachable by query. Nothing ever
    # populated them, which is why it went unnoticed.
    #
    # SET NULL rather than CASCADE: these rows exist for audit and
    # reconciliation, so a plan should outlive the campaign it was generated
    # for rather than disappear with it.
    experiment_id: Mapped[int | None] = mapped_column(
        ForeignKey("experiments.id", ondelete="SET NULL"), nullable=True
    )
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True
    )
    design_type: Mapped[str] = mapped_column(String(32), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Closed-loop round this plan was generated for (1-based); NULL = manual/unassociated.
    round: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        Index("ix_doe_plans_experiment", "experiment_id"),
        Index("ix_doe_plans_campaign", "campaign_id"),
    )


class InferredSystemRow(Base):
    """LLM-inferred formulation-system constraints, persisted for reuse (P2).

    Unknown product_types are matched here before falling back to a fresh LLM
    inference; each hit increments ``hit_count`` so hot entries can be promoted
    into the static knowledge base after human review.
    """

    __tablename__ = "inferred_systems"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Stable cache key = normalize_key(product_type).
    normalized_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    product_type: Mapped[str] = mapped_column(Text, default="")
    system_name: Mapped[str] = mapped_column(String(200), default="")
    must_include_roles: Mapped[list] = mapped_column(JSON, default=list)
    must_exclude: Mapped[str] = mapped_column(Text, default="")
    constraints: Mapped[list] = mapped_column(JSON, default=list)
    metric_ranges: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[str] = mapped_column(String(10), default="medium")
    hit_count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    # Provenance: which requirement first triggered this inference.
    source_requirement_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    source_requirement_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

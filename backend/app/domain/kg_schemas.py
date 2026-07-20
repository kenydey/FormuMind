"""Knowledge graph P0 — API and retrieval schemas."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .schemas import CompositionStatus, EntityKind, Evidence, EvidenceEntityRef

RetrievalMode = Literal["auto", "semantic", "enumerative", "hybrid"]


class KGChemicalEntity(BaseModel):
    id: str
    canonical_name: str
    cas_no: str | None = None
    formula: str | None = None
    linked_catalog_key: str | None = None
    composition_status: CompositionStatus = "resolved"
    mention_count: int = 0


class KGTradeProductEntity(BaseModel):
    id: str
    trade_name: str
    grade: str = ""
    supplier: str = ""
    composition_status: CompositionStatus = "unknown"
    proprietary: bool = False
    generic_name_hint: str = ""
    linked_chemical_ids: list[str] = Field(default_factory=list)
    mention_count: int = 0


class RetrievalPlan(BaseModel):
    mode: RetrievalMode
    entity_ids: list[str] = Field(default_factory=list)
    trade_only: bool = False
    expanded_terms: list[str] = Field(default_factory=list)


class EntityResolveResponse(BaseModel):
    query: str
    chemicals: list[KGChemicalEntity] = Field(default_factory=list)
    trade_products: list[KGTradeProductEntity] = Field(default_factory=list)
    expanded_entity_ids: list[str] = Field(default_factory=list)
    mode: RetrievalMode = "semantic"
    trade_only: bool = False
    interpretation: str = ""


class EntityResolutionSummary(BaseModel):
    query: str
    chemicals: list[KGChemicalEntity] = Field(default_factory=list)
    trade_products: list[KGTradeProductEntity] = Field(default_factory=list)
    mode: RetrievalMode = "semantic"
    truncated: bool = False


class KGRetrieveRequest(BaseModel):
    query: str
    mode: RetrievalMode = "auto"
    project_id: str | None = None
    scan_limit: int | None = None
    chunk_cap: int | None = None
    llm_cap: int | None = None
    max_sources: int | None = None
    k_semantic: int | None = None


class KGRetrieveStats(BaseModel):
    scan_total: int = 0
    chunks_after_dedupe: int = 0
    chunks_sent_to_llm: int = 0
    mention_hits: int = 0
    semantic_hits: int = 0
    truncated: bool = False
    trade_only: bool = False


class KGRetrieveResponse(BaseModel):
    plan: RetrievalPlan
    evidence: list[Evidence]
    stats: KGRetrieveStats


class KGStats(BaseModel):
    enabled: bool
    entities: int = 0
    mentions: int = 0
    links: int = 0
    entities_by_kind: dict[str, int] = Field(default_factory=dict)


class KGRebuildReport(BaseModel):
    linked_sources: int = 0
    entities_upserted: int = 0
    mentions_upserted: int = 0
    links_created: int = 0


class KGLinkReport(BaseModel):
    source_id: str
    entities_upserted: int = 0
    mentions_upserted: int = 0
    links_created: int = 0

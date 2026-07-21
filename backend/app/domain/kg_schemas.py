"""Knowledge graph P0 — API and retrieval schemas."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from .schemas import CompositionStatus, EntityKind, Evidence, EvidenceEntityRef

RetrievalMode = Literal["auto", "semantic", "enumerative", "hybrid"]


class RelationType(str, Enum):
    """Semantic relations extracted from literature/patent text."""

    SUBSTITUTES = "substitutes"
    SYNERGIZES = "synergizes"
    INHIBITS = "inhibits"
    CORRELATES_POS = "correlates_pos"
    CORRELATES_NEG = "correlates_neg"
    REQUIRES = "requires"


SEMANTIC_RELATION_TYPES = frozenset(m.value for m in RelationType)


class RelationEvidence(BaseModel):
    source_id: str
    chunk_id: str | None = None
    sentence: str = ""
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    extraction_method: str = "rule"


class KGRelationView(BaseModel):
    id: str
    source_entity_id: str
    target_entity_id: str
    relation_type: RelationType
    confidence: float = 0.5
    evidence: list[RelationEvidence] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    is_valid: bool = True
    extraction_method: str = "rule"


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
    top_relations: list[KGRelationView] = Field(default_factory=list)
    mode: RetrievalMode = "semantic"
    trade_only: bool = False
    interpretation: str = ""


class KGPathStep(BaseModel):
    relation: KGRelationView
    entity_id: str
    entity_name: str = ""


class KGPathResponse(BaseModel):
    src_entity_id: str
    dst_entity_id: str
    found: bool = False
    hops: int = 0
    steps: list[KGPathStep] = Field(default_factory=list)


class KGSubstituteCandidate(BaseModel):
    entity_id: str
    entity_name: str = ""
    relation_type: RelationType = RelationType.SUBSTITUTES
    confidence: float = 0.5
    hops: int = 1
    path: list[KGPathStep] = Field(default_factory=list)


class KGSubstituteDiscoverResponse(BaseModel):
    query_entity_id: str
    query_entity_name: str = ""
    substitutes: list[KGSubstituteCandidate] = Field(default_factory=list)


class EntityResolutionSummary(BaseModel):
    query: str
    chemicals: list[KGChemicalEntity] = Field(default_factory=list)
    trade_products: list[KGTradeProductEntity] = Field(default_factory=list)
    top_relations: list[KGRelationView] = Field(default_factory=list)
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
    links_by_type: dict[str, int] = Field(default_factory=dict)


class KGRebuildReport(BaseModel):
    linked_sources: int = 0
    entities_upserted: int = 0
    mentions_upserted: int = 0
    links_created: int = 0
    relations_upserted: int = 0


class KGLinkReport(BaseModel):
    source_id: str
    entities_upserted: int = 0
    mentions_upserted: int = 0
    links_created: int = 0
    relations_upserted: int = 0

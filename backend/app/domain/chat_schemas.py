"""Chat P0 — multi-turn, structured, and clarification schemas."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .kg_schemas import EntityResolutionSummary, KGRetrieveStats
from .schemas import Evidence

ChatResponseFormat = Literal["markdown", "structured"]
ClaimStatus = Literal["supported", "weak", "unsupported"]


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)
    citations: list[Evidence] = Field(default_factory=list)


class ClarifiedEntity(BaseModel):
    term: str = Field(max_length=128)
    resolved: str = Field(max_length=256)
    entity_id: str | None = None


class FormulationHint(BaseModel):
    ingredient: str
    role: str = ""
    typical_range: str = ""
    evidence_ref: str


class StructuredAnswer(BaseModel):
    summary: str
    key_findings: list[str] = Field(default_factory=list)
    formulation_hints: list[FormulationHint] = Field(default_factory=list)
    data_conflicts: list[str] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class StructuredAnswerResponse(BaseModel):
    """Wrapper for LLM structured output."""

    answer: StructuredAnswer


class ClarificationOption(BaseModel):
    ambiguous_term: str
    possible_meanings: list[str] = Field(default_factory=list)
    question: str
    candidate_entity_ids: list[str] = Field(default_factory=list)


class SourcedClaim(BaseModel):
    text: str
    chunk_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    status: ClaimStatus = "unsupported"


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    sources: list[Evidence] = Field(default_factory=list)
    domain: str | None = None
    project_id: str | None = None
    include_entity_resolution: bool = False
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)
    clarified_entities: list[ClarifiedEntity] = Field(default_factory=list, max_length=8)
    response_format: ChatResponseFormat = "markdown"
    attachment_source_ids: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    answer: str
    citations: list[Evidence]
    rag_backend: str = "tfidf"
    kb_chunks_used: int = 0
    entity_resolution: EntityResolutionSummary | None = None
    kg_retrieval_stats: KGRetrieveStats | None = None
    structured: StructuredAnswer | None = None
    clarification: ClarificationOption | None = None
    rewritten_query: str | None = None
    sourced_claims: list[SourcedClaim] | None = None

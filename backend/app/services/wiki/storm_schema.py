"""STORM-style longform report schemas (L2 draft; not Claims/DOE)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class SectionSpec(BaseModel):
    section_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    level: Literal[1, 2] = 1
    core_intent: str = ""
    target_word_count: int = Field(default=800, ge=100, le=4000)
    focal_entities: list[str] = Field(default_factory=list)
    retrieval_queries: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)

    @field_validator("retrieval_queries")
    @classmethod
    def _queries_nonempty_when_present(cls, v: list[str]) -> list[str]:
        return [q.strip() for q in v if (q or "").strip()][:8]


class ReportOutline(BaseModel):
    schema_version: int = 1
    project_id: str
    topic: str
    global_summary_goal: str = ""
    estimated_total_words: int = 0
    perspectives: list[str] = Field(default_factory=list)
    sections: list[SectionSpec] = Field(default_factory=list)
    source: str = "deterministic"  # deterministic | llm

    @field_validator("sections")
    @classmethod
    def _require_sections(cls, v: list[SectionSpec]) -> list[SectionSpec]:
        if not v:
            raise ValueError("outline must contain at least one section")
        return v


class SectionDraft(BaseModel):
    section_id: str
    content_markdown: str
    used_citations: list[str] = Field(default_factory=list)
    summary: str = ""
    word_count: int = 0
    model: str = ""
    source: str = "deterministic"  # deterministic | llm


class StormReportState(BaseModel):
    schema_version: int = 1
    project_id: str
    task_id: str = ""
    stage: str = "pending"
    outline: ReportOutline | None = None
    drafts: dict[str, SectionDraft] = Field(default_factory=dict)
    final_path: str | None = None
    final_markdown: str | None = None
    error: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


DEFAULT_PERSPECTIVES: tuple[str, ...] = (
    "配方化学家",
    "盐雾与耐久测试工程师",
    "工艺与 VOC 合规审查",
    "专利与文献策展",
    "成本与供应风险",
)

"""深度研究统一数据模型 — 跨 OpenAlex / EPO / USPTO / arXiv 等 API 的归一化结构。"""
from __future__ import annotations

from collections import Counter
from enum import Enum

from pydantic import BaseModel, Field

from ...domain.schemas import Evidence


class DocumentType(str, Enum):
    """文献或专利类型标识。"""

    LITERATURE = "文献"
    PATENT = "专利"


_PATENT_SOURCES = frozenset({
    "uspto", "epo", "patent", "google patents", "google patents cn", "cnipa",
})
_LITERATURE_SOURCES = frozenset({
    "arxiv", "semantic scholar", "literature", "openalex", "chemcrow-lit", "doi",
    "serpapi scholar", "tavily",
})


class RetrievalHit(BaseModel):
    """单条多源检索命中记录，统一各 API 的字段差异。"""

    title: str = Field(description="标题")
    abstract: str = Field(default="", description="摘要或片段正文")
    authors: list[str] = Field(default_factory=list, description="文献作者列表")
    inventors: list[str] = Field(default_factory=list, description="专利发明人列表")
    source: str = Field(description="来源标识，如 OpenAlex / USPTO / arXiv")
    url: str = Field(default="", description="原文链接")
    date: str | None = Field(default=None, description="发布或公开日期")
    doc_type: DocumentType = Field(description="文献或专利")
    identifier: str = Field(default="", description="唯一标识符（DOI、专利号等）")
    relevance: float = Field(default=0.5, ge=0.0, le=1.0, description="相关性得分")

    def to_evidence(self) -> Evidence:
        return Evidence(
            source=self.source,
            identifier=self.identifier or self.url or self.title,
            title=self.title,
            snippet=self.abstract[:500],
            relevance=self.relevance,
        )

    @classmethod
    def from_evidence(cls, ev: Evidence) -> RetrievalHit:
        src_lower = (ev.source or "").lower()
        if any(k in src_lower for k in _PATENT_SOURCES):
            doc_type = DocumentType.PATENT
        elif any(k in src_lower for k in _LITERATURE_SOURCES):
            doc_type = DocumentType.LITERATURE
        else:
            doc_type = DocumentType.LITERATURE

        url = ev.identifier if ev.identifier.startswith(("http", "doi:", "DOI:")) else ""

        return cls(
            title=ev.title,
            abstract=ev.snippet,
            source=ev.source,
            url=url,
            doc_type=doc_type,
            identifier=ev.identifier,
            relevance=ev.relevance,
        )


# 向后兼容别名（Phase 1 命名）
ResearchResult = RetrievalHit


def to_evidence_list(results: list[RetrievalHit]) -> list[Evidence]:
    return [r.to_evidence() for r in results]


class ExpandedQuery(BaseModel):
    """QueryExpander 输出的结构化查询扩展。"""

    intent: str = Field(description="推断的用户检索意图")
    chinese_keywords: list[str] = Field(default_factory=list, description="中文关键词列表")
    english_synonyms: list[str] = Field(
        default_factory=list, description="英文同义词 / 学术词汇列表"
    )
    ipc_cpc_suggestions: list[str] = Field(
        default_factory=list, description="IPC/CPC 专利分类号建议"
    )


class RetrievalReport(BaseModel):
    """多源检索汇总（检索阶段输出，不含 Markdown 合成报告）。"""

    topic: str = Field(description="用户原始研究主题")
    expanded_query: ExpandedQuery | None = Field(default=None, description="查询扩展结果")
    results: list[RetrievalHit] = Field(default_factory=list, description="归一化检索命中列表")
    source_counts: dict[str, int] = Field(default_factory=dict, description="各来源命中数量统计")
    engine: str = Field(default="offline", description="引擎模式：llm / offline")

    @classmethod
    def from_results(
        cls,
        topic: str,
        results: list[RetrievalHit],
        expanded: ExpandedQuery | None = None,
        engine: str = "offline",
    ) -> RetrievalReport:
        counts = dict(Counter(r.source for r in results))
        return cls(
            topic=topic,
            expanded_query=expanded,
            results=results,
            source_counts=counts,
            engine=engine,
        )

    def to_evidence(self) -> list[Evidence]:
        return to_evidence_list(self.results)


ResearchReport = RetrievalReport

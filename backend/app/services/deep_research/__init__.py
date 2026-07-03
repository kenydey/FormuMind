"""深度研究引擎 — 统一多源检索、查询扩展与报告合成。"""
from .engine import DeepResearchEngine, conduct_research
from .models import (
    DocumentType,
    ExpandedQuery,
    RetrievalHit,
    RetrievalReport,
    ResearchReport,
    ResearchResult,
)
from .query_expander import QueryExpander

__all__ = [
    "DeepResearchEngine",
    "conduct_research",
    "DocumentType",
    "ExpandedQuery",
    "QueryExpander",
    "RetrievalHit",
    "RetrievalReport",
    "ResearchReport",
    "ResearchResult",
]

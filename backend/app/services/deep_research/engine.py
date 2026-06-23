"""深度研究引擎骨架 — 查询扩展 → 多源检索 → 报告汇总（Phase 2+ 接入 OpenAlex/EPO/USPTO）。"""
from __future__ import annotations

import logging
from typing import Callable

import httpx

from ...config import Settings, get_settings
from ...domain.schemas import Requirement
from .. import literature
from .models import ExpandedQuery, ResearchReport, ResearchResult
from .query_expander import QueryExpander

logger = logging.getLogger(__name__)


class DeepResearchEngine:
    """深度研究核心编排器。

    Phase 1：QueryExpander + 现有 literature 多源检索。
    Phase 2+：OpenAlex / EPO OPS / USPTO Open Data HTTP 适配器。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._expander = QueryExpander(self._settings)
        self._http = httpx.Client(timeout=30.0)

        # Phase 2 预留：外部知识库 API 配置
        self._openalex_mailto: str | None = self._settings.openalex_mailto
        self._epo_consumer_key: str | None = self._settings.epo_consumer_key
        self._epo_consumer_secret: str | None = self._settings.epo_consumer_secret
        self._uspto_api_key: str | None = self._settings.uspto_api_key

    def close(self) -> None:
        """释放 HTTP 客户端资源。"""
        self._http.close()

    def __enter__(self) -> DeepResearchEngine:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def expand_query(self, topic: str) -> ExpandedQuery:
        """将用户自然语言主题扩展为结构化检索查询。"""
        return self._expander.expand(topic)

    @staticmethod
    def build_search_query(expanded: ExpandedQuery, topic: str = "") -> str:
        """将扩展结果拼接为 literature 检索可用的组合查询字符串。"""
        parts: list[str] = []
        if topic.strip():
            parts.append(topic.strip())
        parts.extend(expanded.chinese_keywords)
        parts.extend(expanded.english_synonyms)
        parts.extend(expanded.ipc_cpc_suggestions)
        return " ".join(dict.fromkeys(p for p in parts if p))

    def search(
        self,
        topic: str,
        source_types: list[str] | None = None,
        req: Requirement | None = None,
        total_limit: int | None = None,
        per_source_cap: int | None = None,
        progress_cb: Callable[[list[ResearchResult]], None] | None = None,
    ) -> ResearchReport:
        """Phase 1 检索流程：扩展 query → literature.iter_search → ResearchReport。"""
        expanded = self.expand_query(topic)
        combined_query = self.build_search_query(expanded, topic)
        types = source_types or ["patents", "literature", "internet"]
        limit = total_limit or self._settings.search_total_limit
        cap = per_source_cap or self._settings.search_limit_per_source

        def _on_progress(evidence_list) -> None:
            if progress_cb is not None:
                progress_cb([ResearchResult.from_evidence(e) for e in evidence_list])

        evidence = literature.iter_search(
            combined_query,
            types,
            req=req,
            total_limit=limit,
            per_source_cap=cap,
            progress_cb=_on_progress if progress_cb else None,
        )

        results = [ResearchResult.from_evidence(e) for e in evidence]
        engine = "llm" if self._settings.get_active_api_key() else "offline"

        return ResearchReport.from_results(
            topic=topic,
            results=results,
            expanded=expanded,
            engine=engine,
        )

    def run(
        self,
        topic: str,
        req: Requirement | None = None,
        progress_cb: Callable[[float, str], None] | None = None,
    ) -> ResearchReport:
        """完整深度研究工作流入口（Phase 2+ 将加入 RAG 融合与报告合成）。"""
        if progress_cb:
            progress_cb(0.1, "expanding query")

        expanded = self.expand_query(topic)

        if progress_cb:
            progress_cb(0.25, "multi-source retrieval")

        report = self.search(topic, req=req)

        if progress_cb:
            progress_cb(1.0, "done")

        # 确保 expanded_query 写入报告（search 已包含，此处为显式保障）
        if report.expanded_query is None:
            report = report.model_copy(update={"expanded_query": expanded})

        return report

    # ── Phase 2 占位：外部知识库 HTTP 适配器 ────────────────────────────────

    def _search_openalex(self, query: str, limit: int = 10) -> list[ResearchResult]:
        """OpenAlex 文献检索（Phase 2 实现）。"""
        logger.debug("OpenAlex search not yet implemented (query=%s)", query[:80])
        return []

    def _search_epo(self, query: str, limit: int = 10) -> list[ResearchResult]:
        """EPO OPS 专利检索（Phase 2 实现）。"""
        logger.debug("EPO search not yet implemented (query=%s)", query[:80])
        return []

    def _search_uspto(self, query: str, limit: int = 10) -> list[ResearchResult]:
        """USPTO Open Data 专利检索（Phase 2 实现）。"""
        logger.debug("USPTO search not yet implemented (query=%s)", query[:80])
        return []

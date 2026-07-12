"""深度研究引擎 — 查询扩展 → 多源检索 → RAG 融合 → 引用报告。"""
from __future__ import annotations

from ..errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal
import logging
from typing import Callable

import httpx

from ...config import Settings, get_settings
from ...domain import knowledge
from ...domain.schemas import ComprehensiveReport, Evidence, Requirement
from .. import literature, llm, rag
from .models import ExpandedQuery, RetrievalHit, RetrievalReport
from .query_expander import QueryExpander, prepare_search_queries

logger = logging.getLogger(__name__)

_DEFAULT_SOURCE_TYPES = ["patents", "literature", "internet"]


def _dedupe(evidence: list[Evidence]) -> list[Evidence]:
    seen: set[str] = set()
    out: list[Evidence] = []
    for e in evidence:
        key = e.identifier or e.title
        if key and key not in seen:
            seen.add(key)
            out.append(e)
    return out


def _cross_validate_prompt(topic: str, kb_answer: str, evidence: list[Evidence]) -> str:
    citations = "\n".join(
        f"[{e.source}] {e.title}: {e.snippet[:300]}" for e in evidence[:12]
    )
    return (
        "你是资深材料信息学研究员，需要把多源检索结果融合成一份带严格引用的研究报告。\n"
        f"研究主题：{topic}\n\n"
        f"知识库智能体的初步综述：\n{kb_answer}\n\n"
        f"可引用的证据（仅可使用以下事实）：\n{citations}\n\n"
        "撰写要求（必须严格遵守）：\n"
        "1. 每条技术论断后用 [来源标识] 标注其依据（如 [USPTO]、[arXiv]、[Internet]）；\n"
        "2. 只能使用上方证据中出现的事实，缺乏证据支撑的地方必须显式写「证据不足」，禁止编造数据/机理；\n"
        "3. 若不同来源数据冲突，明确指出冲突并说明取舍理由；\n"
        "4. 用简体中文，分「关键发现」「配方参数线索」「机理」「数据冲突与不确定性」四节，Markdown 格式。"
    )


def _offline_report(topic: str, evidence: list[Evidence]) -> str:
    if not evidence:
        return f"# {topic}\n\n证据不足：当前未检索到可引用的资料。请安装 `intel` extra 或上传本地文件后重试。"
    lines = [f"# {topic}", "", f"基于 {len(evidence)} 条检索证据的归纳（离线模式，未启用 LLM 合成）：", ""]
    lines.append("## 关键发现")
    for e in evidence[:8]:
        lines.append(f"- {e.title} [{e.source}] — {e.snippet[:160]}")
    lines.append("")
    lines.append("## 数据冲突与不确定性")
    lines.append("- 离线模式未做跨源交叉验证；请配置 LLM 后运行深度研究以获得冲突标注与机理综合。")
    return "\n".join(lines)


class DeepResearchEngine:
    """统一深度研究编排器：检索 + RAG + 引用报告。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._expander = QueryExpander(self._settings)
        self._http = httpx.Client(
            timeout=30.0,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        self._openalex_mailto: str | None = self._settings.openalex_mailto
        self._epo_consumer_key: str | None = self._settings.epo_consumer_key
        self._epo_consumer_secret: str | None = self._settings.epo_consumer_secret
        self._uspto_api_key: str | None = self._settings.uspto_api_key

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> DeepResearchEngine:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def expand_query(self, topic: str) -> ExpandedQuery:
        return self._expander.expand(topic)

    def retrieve(
        self,
        topic: str,
        source_types: list[str] | None = None,
        req: Requirement | None = None,
        total_limit: int | None = None,
        per_source_cap: int | None = None,
        progress_cb: Callable[[list[Evidence]], None] | None = None,
    ) -> tuple[list[Evidence], ExpandedQuery]:
        """QueryExpander + iter_search 多源检索。"""
        expanded = self.expand_query(topic)
        from .query_expander import prepare_search_queries

        sq = prepare_search_queries(topic, self._settings)
        combined_query = sq.rank_q
        types = source_types or _DEFAULT_SOURCE_TYPES
        limit = total_limit or self._settings.search_total_limit
        cap = per_source_cap or self._settings.search_limit_per_source

        evidence = literature.iter_search(
            combined_query,
            types,
            req=req,
            total_limit=limit,
            per_source_cap=cap,
            progress_cb=progress_cb,
        )

        # Full-text acquisition: upgrade the top fetchable hits (patent PDF /
        # OA literature PDF / web page body) to full-document chunks and
        # persist the raw text. No-op unless FORMUMIND_FULLTEXT_ENRICH=true.
        if self._settings.fulltext_enrich and evidence:
            from ..fulltext_fetcher import enrich_search_results

            evidence, _report = enrich_search_results(evidence)
            if progress_cb is not None:
                try:
                    progress_cb(evidence)
                except TypeError:
                    pass
        return evidence, expanded

    def search(
        self,
        topic: str,
        source_types: list[str] | None = None,
        req: Requirement | None = None,
        total_limit: int | None = None,
        per_source_cap: int | None = None,
        progress_cb: Callable[[list[RetrievalHit]], None] | None = None,
    ) -> RetrievalReport:
        def _on_progress(evidence_list: list[Evidence]) -> None:
            if progress_cb is not None:
                progress_cb([RetrievalHit.from_evidence(e) for e in evidence_list])

        evidence, expanded = self.retrieve(
            topic,
            source_types=source_types,
            req=req,
            total_limit=total_limit,
            per_source_cap=per_source_cap,
            progress_cb=_on_progress if progress_cb else None,
        )
        results = [RetrievalHit.from_evidence(e) for e in evidence]
        engine = "llm" if self._settings.get_active_api_key() else "offline"
        return RetrievalReport.from_results(topic, results, expanded=expanded, engine=engine)

    def kb_agent(
        self,
        topic: str,
        retrieval_query: str,
        evidence: list[Evidence],
        domain: str | None = None,
        k: int = 6,
    ) -> tuple[str, list[Evidence]]:
        """RAG：ColBERT search + LLM rerank → grounded synthesis."""
        from .. import colbert_store

        if evidence:
            colbert_store.index_evidence(evidence)
        hits = colbert_store.search(retrieval_query, k=min(k * 2, max(k, len(evidence) or k)))
        ranked = [h.evidence for h in hits] or evidence
        ranked = rag.llm_rerank(topic, ranked, k=k)
        answer, citations = llm.answer_question(topic, ranked, domain)
        return answer, citations

    def report_agent(
        self,
        topic: str,
        kb_answer: str,
        evidence: list[Evidence],
    ) -> tuple[str, list[Evidence], str]:
        merged = _dedupe(evidence)
        report = None
        if merged:
            try:
                report = llm._call_llm(_cross_validate_prompt(topic, kb_answer, merged))
            except Exception:
                report = None
        if report:
            return report, merged, "llm"
        return _offline_report(topic, merged), merged, "offline"

    def run(
        self,
        topic: str,
        req: Requirement | None = None,
        source_types: list[str] | None = None,
        progress_cb: Callable[[float, str], None] | None = None,
        retrieval_progress_cb: Callable[[list[Evidence]], None] | None = None,
    ) -> ComprehensiveReport:
        """完整深度研究 — delegates to CRAG research graph."""
        from ...pipeline.research_graph import run_research_graph

        stage_map = {
            "retrieve": (0.2, "正在检索"),
            "grade": (0.45, "评估质量"),
            "fallback": (0.55, "重试搜索"),
            "generate": (0.7, "生成答案"),
            "claim_check": (0.82, "核验论断"),
            "regenerate": (0.86, "修正报告"),
            "recommend": (0.9, "推荐配方"),
        }

        def graph_progress(stage: str, message: str, partial: dict | None = None) -> None:
            if retrieval_progress_cb and stage == "retrieve" and partial:
                pass
            p, _ = stage_map.get(stage, (0.5, message))
            if progress_cb:
                progress_cb(p, message)

        state = run_research_graph(
            topic=topic,
            req=req,
            query=topic,
            progress_cb=graph_progress,
        )
        grounded = state.get("grounded_evidence") or []
        if progress_cb:
            progress_cb(1.0, "done")
        return ComprehensiveReport(
            topic=topic,
            report_markdown=state.get("report_markdown") or state.get("answer") or "",
            citations=state.get("citations") or grounded,
            candidates=state.get("recommended") or [],
            web_count=0,
            kb_count=len(grounded),
            engine=state.get("recommend_engine") or "offline",
            verified_claims=state.get("verified_claims") or [],
            claim_check_engine="offline",
            claim_check_pass_rate=float(state.get("claim_check_pass_rate") or 1.0),
        )


def conduct_research(
    topic: str,
    req: Requirement | None = None,
    source_types: list[str] | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
    retrieval_progress_cb: Callable[[list[Evidence]], None] | None = None,
) -> ComprehensiveReport:
    """Module-level entry point for tasks and scripts."""
    return DeepResearchEngine().run(
        topic,
        req=req,
        source_types=source_types,
        progress_cb=progress_cb,
        retrieval_progress_cb=retrieval_progress_cb,
    )

"""深度研究引擎 — 查询扩展 → 多源检索 → RAG 融合 → 引用报告。"""
from __future__ import annotations

import logging
from typing import Callable

import httpx

from ...config import Settings, get_settings
from ...domain import knowledge
from ...domain.schemas import ComprehensiveReport, Evidence, Requirement
from .. import literature, llm, rag
from .models import ExpandedQuery, RetrievalHit, RetrievalReport
from .query_expander import QueryExpander, build_search_query

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
        self._http = httpx.Client(timeout=30.0)
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
        combined_query = build_search_query(expanded, topic)
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
        """RAG：扩展查询检索 → LLM 重排 → 接地合成（不使用 HyDE）。"""
        if not evidence:
            return "", []
        store = rag.build_store()
        store.ingest(evidence)
        ranked = store.query(retrieval_query, k=min(k * 2, len(evidence)))
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
        """完整深度研究：扩展 → 多源检索 → RAG → 引用报告。"""

        def _progress(p: float, msg: str) -> None:
            if progress_cb:
                progress_cb(p, msg)

        domain = req.domain.value if req else None
        types = source_types or _DEFAULT_SOURCE_TYPES

        _progress(0.1, "expanding query & retrieving evidence")
        evidence, expanded = self.retrieve(
            topic,
            source_types=types,
            req=req,
            total_limit=min(120, self._settings.search_total_limit),
            per_source_cap=min(30, self._settings.search_limit_per_source),
            progress_cb=retrieval_progress_cb,
        )

        if self._settings.pdf_download and evidence:
            from .. import pdf_downloader as _pdf

            evidence = _pdf.enrich_with_fulltext(
                evidence, max_pdfs=self._settings.pdf_download_max
            )

        retrieval_query = build_search_query(expanded, topic)
        web = [e for e in evidence if "internet" in (e.source or "").lower() or "web" in (e.source or "").lower()]

        _progress(0.55, "kb agent: re-rank + grounded synthesis")
        kb_answer, kb_ev = self.kb_agent(topic, retrieval_query, _dedupe(evidence), domain)

        _progress(0.85, "report agent: cross-validation & citation")
        report_md, citations, engine = self.report_agent(topic, kb_answer, _dedupe(evidence + kb_ev))

        candidates: list = []
        if req:
            from .. import llm
            from ..domain.formulation_gate import recommended_to_formulation
            from ..domain.objective_contract import normalize_objectives

            rec = llm.recommend_formulations(req, normalize_objectives(req), _dedupe(evidence), n=3)
            for f in rec.formulas:
                try:
                    candidates.append(recommended_to_formulation(f))
                except ValueError:
                    continue

        _progress(1.0, "done")
        return ComprehensiveReport(
            topic=topic,
            report_markdown=report_md,
            citations=citations,
            candidates=candidates,
            web_count=len(web),
            kb_count=len(kb_ev),
            engine=engine,
        )

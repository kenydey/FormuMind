"""Knowledge-cohort multi-agent deep research orchestrator.

A "knowledge cohort" coordinates three cooperating agents to answer a research
topic at NotebookLM-level quality, then cross-validates their findings into a
single citation-grounded report:

* **Web agent**   — latest market / supply-chain signal via DuckDuckGo (ddgs).
* **KB agent**    — advanced RAG over downloaded patents + prior art: HyDE query
  expansion → semantic/TF-IDF retrieval → LLM re-rank → grounded synthesis
  (ChemCrow → paper-qa → RAG → offline, via ``llm.answer_question``).
* **Report agent** — dialectically fuses web + KB evidence under strict
  anti-hallucination constraints (every claim must cite a source; conflicts are
  flagged; missing evidence is stated, never invented).

Every step reuses existing services and degrades gracefully: with no optional
libraries or LLM key configured, the cohort still returns a report grounded in
the offline seed corpus (``engine="offline"``). Nothing here introduces a new
agent framework — it is plain orchestration over the modules already in place.
"""
from __future__ import annotations

from typing import Callable

from ..domain import knowledge
from ..domain.schemas import ComprehensiveReport, Evidence, Requirement
from . import literature, llm, rag


def _dedupe(evidence: list[Evidence]) -> list[Evidence]:
    """De-duplicate by identifier (falling back to title), keep first occurrence."""
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
    """Deterministic report when no LLM is configured — grounded in citations only."""
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


class KnowledgeCohort:
    """Coordinate web / KB / report agents into one cross-validated report."""

    def web_agent(self, topic: str, limit: int = 5) -> list[Evidence]:
        """Latest market / supply-chain evidence via DuckDuckGo.

        Routed through ``search_by_types(["internet"])`` so it inherits the
        concurrent 10 s per-source timeout — a slow/hung network call can never
        stall the whole research task. Empty list when ddgs is absent.
        """
        return literature.search_by_types(topic, ["internet"], limit_per_source=limit)

    def kb_agent(
        self, topic: str, evidence: list[Evidence], domain: str | None = None, k: int = 6
    ) -> tuple[str, list[Evidence]]:
        """Advanced RAG: HyDE → semantic retrieval → LLM re-rank → grounded synthesis."""
        if not evidence:
            return "", []
        expanded = rag.hyde_expand(topic, domain)
        store = rag.build_store()
        store.ingest(evidence)
        ranked = store.query(expanded, k=min(k * 2, len(evidence)))
        ranked = rag.llm_rerank(topic, ranked, k=k)
        answer, citations = llm.answer_question(topic, ranked, domain)
        return answer, citations

    def report_agent(
        self,
        topic: str,
        web_ev: list[Evidence],
        kb_answer: str,
        kb_ev: list[Evidence],
    ) -> tuple[str, list[Evidence], str]:
        """Cross-validate web + KB evidence into a cited report. Returns (md, citations, engine)."""
        merged = _dedupe(web_ev + kb_ev)
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
        progress_cb: Callable[[float, str], None] | None = None,
    ) -> ComprehensiveReport:
        """Run the full cohort: retrieve → advanced RAG → cross-validated report."""

        def _progress(p: float, msg: str) -> None:
            if progress_cb:
                progress_cb(p, msg)

        domain = req.domain.value if req else None

        _progress(0.1, "fetching patents / prior art")
        if req:
            patents = literature.search_patents(req, limit=8)
        else:
            patents = literature.search_by_types(topic, ["patents"], limit_per_source=8)

        # Optionally enrich top patents with full-text PDF content.
        # Gated by pdf_download=false so CI tests stay fully offline.
        from ..config import get_settings as _get_settings
        _cfg = _get_settings()
        if _cfg.pdf_download and patents:
            from . import pdf_downloader as _pdf
            patents = _pdf.enrich_with_fulltext(
                patents, max_pdfs=_cfg.pdf_download_max
            )

        _progress(0.35, "web agent: market & supply-chain signal")
        web = self.web_agent(topic)

        _progress(0.6, "kb agent: HyDE + re-rank + grounded synthesis")
        kb_answer, kb_ev = self.kb_agent(topic, _dedupe(patents + web), domain)

        _progress(0.85, "report agent: cross-validation & citation")
        report_md, citations, engine = self.report_agent(topic, web, kb_answer, kb_ev)

        candidates = knowledge.variant_formulations(req, n=3) if req else []

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


def conduct_research(
    topic: str,
    req: Requirement | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
) -> ComprehensiveReport:
    """Module-level convenience entry point for the task layer."""
    return KnowledgeCohort().run(topic, req=req, progress_cb=progress_cb)

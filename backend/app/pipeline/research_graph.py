"""CRAG research graph — ColBERT retrieve → grade → fallback → generate."""
from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any, Literal, TypedDict

from loguru import logger
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..domain.schemas import Evidence, Formulation, Requirement, ResearchResult
from ..services import colbert_store, llm
from ..services.federated_search import FederatedSearchEngine
from ..services.rag import llm_rerank


class GradeVerdict(str, Enum):
    correct = "correct"
    incorrect = "incorrect"


class DocGrade(BaseModel):
    index: int
    relevant: bool
    score: float = 0.5


class GradeResult(BaseModel):
    verdict: GradeVerdict
    reason: str = ""
    doc_grades: list[DocGrade] = Field(default_factory=list)


class GroundedEvidenceResult(BaseModel):
    query: str
    evidence: list[Evidence]
    grounded_evidence: list[Evidence]
    grade: GradeVerdict
    grade_reason: str = ""
    fallback_used: bool = False


class ResearchGraphState(TypedDict, total=False):
    topic: str
    query: str
    req: Requirement | None
    pre_index: list[Evidence]
    evidence: list[Evidence]
    grounded_evidence: list[Evidence]
    grade: GradeVerdict | None
    grade_reason: str
    fallback_used: bool
    answer: str
    citations: list[Evidence]
    recommended: list[Formulation]
    mechanism: str
    chat_markdown: str
    recommend_engine: str
    stage: str
    report_markdown: str


ProgressCallback = Callable[[str, str, dict[str, Any] | None], None]


def _emit(cb: ProgressCallback | None, stage: str, message: str, partial: dict | None = None) -> None:
    if cb:
        cb(stage, message, partial)


def _grade_prompt(topic: str, evidence: list[Evidence]) -> str:
    lines = "\n".join(
        f"[{i}] ({e.source}) {e.title}: {e.snippet[:200]}"
        for i, e in enumerate(evidence[:12])
    )
    return (
        "你是 CRAG 知识评估器。判断检索结果是否足以回答研究主题。\n"
        f"研究主题：{topic}\n\n"
        f"检索片段：\n{lines or '(无)'}\n\n"
        "返回 JSON：\n"
        '{"verdict":"correct"|"incorrect","reason":"...","doc_grades":[{"index":0,"relevant":true,"score":0.9},...]}'
    )


def grade_evidence(topic: str, evidence: list[Evidence], settings: Settings | None = None) -> GradeResult:
    settings = settings or get_settings()
    if not evidence:
        return GradeResult(verdict=GradeVerdict.incorrect, reason="无检索结果")

    min_score = settings.colbert_min_score
    try:
        data = llm.complete_json(_grade_prompt(topic, evidence))
    except Exception as exc:
        logger.warning("Grade LLM failed: {}", exc)
        data = None

    if isinstance(data, dict) and data.get("verdict") in ("correct", "incorrect"):
        doc_grades = []
        for item in data.get("doc_grades") or []:
            try:
                doc_grades.append(
                    DocGrade(
                        index=int(item["index"]),
                        relevant=bool(item.get("relevant", False)),
                        score=float(item.get("score", 0.5)),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        verdict = GradeVerdict(data["verdict"])
        return GradeResult(verdict=verdict, reason=str(data.get("reason", "")), doc_grades=doc_grades)

    relevant_count = sum(1 for e in evidence if e.relevance >= min_score)
    if relevant_count >= max(1, len(evidence) // 3):
        return GradeResult(
            verdict=GradeVerdict.correct,
            reason="offline heuristic: sufficient high-score hits",
        )
    return GradeResult(verdict=GradeVerdict.incorrect, reason="offline heuristic: low relevance")


def apply_grade(
    evidence: list[Evidence],
    grade: GradeResult,
    settings: Settings | None = None,
) -> list[Evidence]:
    settings = settings or get_settings()
    min_score = settings.colbert_min_score
    if grade.doc_grades:
        grounded: list[Evidence] = []
        for dg in grade.doc_grades:
            if dg.relevant and dg.score >= min_score and 0 <= dg.index < len(evidence):
                ev = evidence[dg.index]
                grounded.append(ev.model_copy(update={"relevance": dg.score}))
        if grounded:
            return grounded
    return [e for e in evidence if e.relevance >= min_score][: settings.colbert_top_k]


def retrieve_node(state: ResearchGraphState, settings: Settings | None = None) -> ResearchGraphState:
    settings = settings or get_settings()
    query = state.get("query") or state.get("topic") or ""
    pre = state.get("pre_index") or []
    if pre:
        colbert_store.index_evidence(pre, settings=settings)

    colbert_store.bootstrap_seed_corpus(settings)
    hits = colbert_store.search(query, settings=settings)
    evidence = [h.evidence for h in hits]
    evidence = llm_rerank(query, evidence, k=settings.colbert_top_k)
    state["evidence"] = evidence
    state["stage"] = "retrieve"
    return state


def grade_node(state: ResearchGraphState, settings: Settings | None = None) -> ResearchGraphState:
    settings = settings or get_settings()
    topic = state.get("topic") or state.get("query") or ""
    evidence = state.get("evidence") or []
    grade = grade_evidence(topic, evidence, settings)
    grounded = apply_grade(evidence, grade, settings)
    state["grade"] = grade.verdict
    state["grade_reason"] = grade.reason
    state["grounded_evidence"] = grounded
    state["stage"] = "grade"
    return state


def fallback_node(
    state: ResearchGraphState,
    settings: Settings | None = None,
    *,
    mode: Literal["recommend", "deep"] = "deep",
) -> ResearchGraphState:
    settings = settings or get_settings()
    query = state.get("query") or state.get("topic") or ""
    req = state.get("req")

    if mode == "recommend":
        from ..services import literature

        fed = FederatedSearchEngine(settings)
        types = fed.effective_sources()
        evidence = literature.iter_search(
            query,
            types,
            req=req,
            total_limit=30,
            per_source_cap=10,
            max_rounds=1,
        )
    else:
        fed = FederatedSearchEngine(settings)
        result = fed.search(query, req=req)
        evidence = result.evidence

    if evidence:
        colbert_store.index_evidence(evidence, settings=settings)
        state["evidence"] = evidence
    state["fallback_used"] = True
    state["stage"] = "fallback"
    return state


def recommend_generate_node(state: ResearchGraphState, settings: Settings | None = None) -> ResearchGraphState:
    """Lightweight recommend path — skip answer/report/synthesize LLM calls."""
    from ..domain.formulation_gate import recommended_to_formulation, validate_formulations
    from ..domain.objective_contract import normalize_objectives
    from ..pipeline.workflow import _score_and_validate, process_for

    settings = settings or get_settings()
    req = state.get("req")
    grounded = state.get("grounded_evidence") or state.get("evidence") or []

    mechanism = ""
    chat = ""
    recommended: list[Formulation] = []
    recommend_engine = "offline"

    if req:
        rec_resp = llm.recommend_formulations(req, normalize_objectives(req), grounded, n=3)
        recommend_engine = rec_resp.engine
        process = process_for(req)
        forms = []
        for rec in rec_resp.formulas:
            try:
                forms.append(recommended_to_formulation(rec))
            except ValueError as exc:
                rec_resp.warnings.append(str(exc))
        recommended = [_score_and_validate(f, process, req) for f in forms]
        recommended, gate_warnings = validate_formulations(recommended)
        recommended.sort(key=lambda f: (f.score or 0.0), reverse=True)
        if recommended:
            mechanism = recommended[0].rationale or ""
            chat = f"已推荐 {len(recommended)} 条配方。"
        else:
            chat = "未能生成有效配方。"
        if gate_warnings:
            chat += "\n\n**Formulation validation:**\n" + "\n".join(f"- {w}" for w in gate_warnings)
    else:
        chat = "缺少需求参数，无法推荐配方。"

    state["mechanism"] = mechanism
    state["chat_markdown"] = chat
    state["recommended"] = recommended
    state["recommend_engine"] = "llm" if recommend_engine == "llm" else "offline"
    state["stage"] = "recommend"
    return state


def generate_node(state: ResearchGraphState, settings: Settings | None = None) -> ResearchGraphState:
    from ..domain.formulation_gate import recommended_to_formulation, validate_formulations
    from ..domain.objective_contract import normalize_objectives
    from ..pipeline.workflow import _score_and_validate, process_for
    from ..services.deep_research.engine import DeepResearchEngine

    settings = settings or get_settings()
    topic = state.get("topic") or state.get("query") or ""
    req = state.get("req")
    grounded = state.get("grounded_evidence") or state.get("evidence") or []
    domain = req.domain.value if req else None

    answer, citations = llm.answer_question(topic, grounded, domain)
    state["answer"] = answer
    state["citations"] = citations or grounded

    engine = DeepResearchEngine(settings)
    report_md, report_citations, report_engine = engine.report_agent(topic, answer, grounded)
    state["report_markdown"] = report_md
    state["citations"] = report_citations or grounded

    mechanism = ""
    chat = answer
    recommended: list[Formulation] = []
    recommend_engine = "offline"

    if req:
        rec_resp = llm.recommend_formulations(req, normalize_objectives(req), grounded, n=3)
        recommend_engine = rec_resp.engine
        process = process_for(req)
        forms = []
        for rec in rec_resp.formulas:
            try:
                forms.append(recommended_to_formulation(rec))
            except ValueError as exc:
                rec_resp.warnings.append(str(exc))
        recommended = [_score_and_validate(f, process, req) for f in forms]
        recommended, gate_warnings = validate_formulations(recommended)
        recommended.sort(key=lambda f: (f.score or 0.0), reverse=True)
        mechanism, chat = llm.synthesize_research(req, grounded, recommended)
        if gate_warnings:
            chat += "\n\n**Formulation validation:**\n" + "\n".join(
                f"- {w}" for w in gate_warnings
            )

    state["mechanism"] = mechanism
    state["chat_markdown"] = chat
    state["recommended"] = recommended
    state["recommend_engine"] = "llm" if recommend_engine == "llm" else "offline"
    state["stage"] = "generate"
    return state


def route_after_grade(state: ResearchGraphState) -> str:
    if state.get("grade") == GradeVerdict.incorrect and not state.get("fallback_used"):
        return "fallback"
    return "generate"


def _needs_fallback(state: ResearchGraphState, mode: Literal["recommend", "deep"]) -> bool:
    if state.get("grade") != GradeVerdict.incorrect or state.get("fallback_used"):
        return False
    if mode == "recommend" and (state.get("pre_index") or []):
        return False
    return True


def _run_crag_retrieval(
    state: ResearchGraphState,
    settings: Settings,
    mode: Literal["recommend", "deep"],
    progress_cb: ProgressCallback | None,
) -> ResearchGraphState:
    _emit(progress_cb, "retrieve", "正在检索")
    state = retrieve_node(state, settings)
    _emit(
        progress_cb,
        "retrieve",
        f"已召回 {len(state.get('evidence') or [])} 条",
        {"evidence_count": len(state.get("evidence") or [])},
    )

    _emit(progress_cb, "grade", "评估质量")
    state = grade_node(state, settings)

    if _needs_fallback(state, mode):
        _emit(progress_cb, "fallback", "重试搜索")
        state = fallback_node(state, settings, mode=mode)
        state = retrieve_node(state, settings)
        state = grade_node(state, settings)

    return state


def run_research_graph(
    topic: str,
    req: Requirement | None = None,
    *,
    query: str = "",
    pre_index: list[Evidence] | None = None,
    progress_cb: ProgressCallback | None = None,
    settings: Settings | None = None,
    mode: Literal["recommend", "deep"] = "recommend",
) -> ResearchGraphState:
    """Execute CRAG pipeline (LangGraph-compatible linear runner)."""
    settings = settings or get_settings()
    q = query or topic or (req.headline() if req else "")

    state: ResearchGraphState = {
        "topic": topic,
        "query": q,
        "req": req,
        "pre_index": pre_index or [],
        "fallback_used": False,
    }

    state = _run_crag_retrieval(state, settings, mode, progress_cb)

    if mode == "recommend":
        _emit(progress_cb, "recommend", "推荐配方")
        state = recommend_generate_node(state, settings)
    else:
        _emit(progress_cb, "generate", "生成答案")
        state = generate_node(state, settings)
        _emit(progress_cb, "recommend", "推荐配方")
    return state


def resolve_grounded_evidence(
    req: Requirement,
    query: str,
    *,
    pre_index: list[Evidence] | None = None,
    settings: Settings | None = None,
) -> GroundedEvidenceResult:
    """ColBERT retrieve → CRAG grade → grounded_evidence SSOT."""
    settings = settings or get_settings()
    q = query or req.headline()
    state: ResearchGraphState = {
        "topic": q,
        "query": q,
        "req": req,
        "pre_index": pre_index or [],
        "fallback_used": False,
    }
    state = _run_crag_retrieval(state, settings, "recommend", None)
    return GroundedEvidenceResult(
        query=query,
        evidence=state.get("evidence") or [],
        grounded_evidence=state.get("grounded_evidence") or [],
        grade=state.get("grade") or GradeVerdict.incorrect,
        grade_reason=state.get("grade_reason") or "",
        fallback_used=bool(state.get("fallback_used")),
    )


def graph_state_to_research_result(state: ResearchGraphState, req: Requirement) -> ResearchResult:
    grounded = state.get("grounded_evidence") or []
    return ResearchResult(
        requirement_headline=req.headline(),
        evidence=grounded,
        mechanism=state.get("mechanism") or "",
        recommended=state.get("recommended") or [],
        chat_markdown=state.get("chat_markdown") or state.get("answer") or "",
        recommend_engine=state.get("recommend_engine") or "offline",
    )


def build_langgraph(settings: Settings | None = None):
    """Build LangGraph StateGraph when langgraph is installed."""
    settings = settings or get_settings()
    try:
        from langgraph.graph import END, StateGraph
    except ImportError:
        return None

    graph = StateGraph(ResearchGraphState)

    def _retrieve(s: ResearchGraphState) -> ResearchGraphState:
        return retrieve_node(s, settings)

    def _grade(s: ResearchGraphState) -> ResearchGraphState:
        return grade_node(s, settings)

    def _fallback(s: ResearchGraphState) -> ResearchGraphState:
        return fallback_node(s, settings)

    def _generate(s: ResearchGraphState) -> ResearchGraphState:
        return generate_node(s, settings)

    graph.add_node("retrieve", _retrieve)
    graph.add_node("grade", _grade)
    graph.add_node("fallback", _fallback)
    graph.add_node("generate", _generate)
    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "grade")
    graph.add_conditional_edges("grade", route_after_grade, {"fallback": "fallback", "generate": "generate"})
    graph.add_edge("fallback", "retrieve")
    graph.add_edge("generate", END)
    return graph.compile()

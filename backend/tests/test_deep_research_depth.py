"""What makes deep research deep, and the three places it used to misreport.

Before this, the pipeline ran exactly one retrieval against the topic string. The
grading and claim-checking around it were real, but a single query only finds what
that query's wording reaches — everything the topic implies but does not say was
never searched for. These tests pin the parts that produce genuinely new angles,
and the chain of caps that would otherwise quietly undo them.
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.domain.schemas import Evidence
from app.pipeline import research_graph as rg
from app.pipeline import subquestions as sq


@pytest.fixture(autouse=True)
def _fresh():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _ev(ident: str, rel: float = 0.8) -> Evidence:
    return Evidence(
        title=f"文献 {ident}", snippet="片段" * 50, source="patents",
        identifier=ident, relevance=rel,
    )


# ── decomposition ────────────────────────────────────────────────────────────


def test_topic_is_always_the_first_query(monkeypatch):
    """Sub-questions are additions, not replacements: the topic is the one query
    guaranteed to be what the user actually asked."""
    monkeypatch.setattr(
        "app.services.llm.complete_json",
        lambda p: {"questions": ["树脂体系选择", "固化机理", "盐雾失效模式"]},
    )
    out = sq.decompose("水性环氧防腐涂料配方", 4)
    assert out[0] == "水性环氧防腐涂料配方"
    assert len(out) == 4


def test_decomposition_off_returns_the_topic_alone(monkeypatch):
    called: list[int] = []
    monkeypatch.setattr(
        "app.services.llm.complete_json", lambda p: called.append(1) or {}
    )
    assert sq.decompose("主题", 1) == ["主题"]
    assert called == [], "n<=1 must not cost an LLM call"


def test_offline_degrades_to_a_single_query(monkeypatch):
    """No LLM → one question, identical behaviour to before. It must not invent
    angles from a template: a made-up sub-question spends a retrieval round on
    something nobody asked."""
    monkeypatch.setattr("app.services.llm.complete_json", lambda p: None)
    assert sq.decompose("水性环氧防腐涂料", 4) == ["水性环氧防腐涂料"]


def test_llm_failure_does_not_propagate(monkeypatch):
    def boom(p):
        raise RuntimeError("provider down")

    monkeypatch.setattr("app.services.llm.complete_json", boom)
    assert sq.decompose("主题", 4) == ["主题"]


@pytest.mark.parametrize("junk", ["", "  ", "短", "x" * 200])
def test_unusable_subquestions_are_dropped(monkeypatch, junk):
    """One word retrieves noise; a paragraph is the model ignoring the brief."""
    monkeypatch.setattr(
        "app.services.llm.complete_json", lambda p: {"questions": [junk, "固化机理研究"]}
    )
    out = sq.decompose("主题", 4)
    assert out == ["主题", "固化机理研究"]


def test_duplicates_are_dropped(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm.complete_json",
        lambda p: {"questions": ["固化机理", "固化机理", "主题"]},
    )
    assert sq.decompose("主题", 5) == ["主题", "固化机理"]


# ── merge ────────────────────────────────────────────────────────────────────


def test_merge_interleaves_so_no_question_crowds_the_others():
    """Round-robin, not concatenation: one prolific sub-question must not fill
    the downstream cap by itself — that would waste having asked the others."""
    a = [_ev("a1"), _ev("a2"), _ev("a3")]
    b = [_ev("b1"), _ev("b2")]
    ids = [e.identifier for e in sq.merge_evidence([a, b])]
    assert ids == ["a1", "b1", "a2", "b2", "a3"]


def test_merge_dedupes_and_keeps_the_higher_score():
    """Two questions finding the same document is agreement, i.e. evidence of
    relevance — not a reason to keep the weaker score."""
    merged = sq.merge_evidence([[_ev("same", 0.4)], [_ev("same", 0.9)]])
    assert len(merged) == 1
    assert merged[0].relevance == 0.9


def test_merge_handles_empty_batches():
    assert sq.merge_evidence([]) == []
    assert sq.merge_evidence([[], []]) == []


# ── wiring into retrieval ────────────────────────────────────────────────────


def _stub_search(monkeypatch, per_query: dict[str, list[Evidence]] | None = None):
    seen: list[str] = []

    class _Hit:
        def __init__(self, ev):
            self.evidence = ev

    def search(query, settings=None, k=None):
        seen.append(query)
        if per_query is not None:
            for key, evs in per_query.items():
                if key in query:
                    return [_Hit(e) for e in evs]
            return []
        return [_Hit(_ev(f"doc-{len(seen)}"))]

    monkeypatch.setattr(rg.colbert_store, "search", search)
    monkeypatch.setattr(rg.colbert_store, "bootstrap_seed_corpus", lambda s: None)
    monkeypatch.setattr(rg.colbert_store, "index_evidence", lambda *a, **k: 0)
    monkeypatch.setattr(rg, "llm_rerank", lambda q, ev, k=None: ev[: (k or len(ev))])
    return seen


def test_deep_mode_retrieves_once_per_angle(monkeypatch):
    monkeypatch.setattr(get_settings(), "deep_subquestions", 3, raising=False)
    monkeypatch.setattr(get_settings(), "deep_hyde_enabled", False, raising=False)
    monkeypatch.setattr(get_settings(), "kg_enabled", False, raising=False)
    monkeypatch.setattr(get_settings(), "kb_v2_enabled", False, raising=False)
    monkeypatch.setattr(
        "app.services.llm.complete_json", lambda p: {"questions": ["树脂体系选择", "固化机理研究"]}
    )
    seen = _stub_search(monkeypatch)

    state = rg.retrieve_node({"topic": "主题", "query": "主题"}, mode="deep")
    assert len(seen) == 3, f"expected one search per angle, got {seen}"
    assert state["retrieval_queries"][0] == "主题"


def test_recommend_mode_never_decomposes(monkeypatch):
    """That path has a test asserting it finishes under five seconds offline;
    extra LLM round trips there buy breadth nobody asked for."""
    monkeypatch.setattr(get_settings(), "deep_subquestions", 4, raising=False)
    monkeypatch.setattr(get_settings(), "kg_enabled", False, raising=False)
    monkeypatch.setattr(get_settings(), "kb_v2_enabled", False, raising=False)
    called: list[int] = []
    monkeypatch.setattr(
        "app.services.llm.complete_json", lambda p: called.append(1) or {}
    )
    seen = _stub_search(monkeypatch)

    rg.retrieve_node({"topic": "主题", "query": "主题"}, mode="recommend")
    assert len(seen) == 1
    assert called == []


def test_hyde_runs_once_not_per_angle(monkeypatch):
    """Four angles plus a fallback round would be ten LLM calls instead of two."""
    monkeypatch.setattr(get_settings(), "deep_subquestions", 4, raising=False)
    monkeypatch.setattr(get_settings(), "deep_hyde_enabled", True, raising=False)
    monkeypatch.setattr(get_settings(), "kg_enabled", False, raising=False)
    monkeypatch.setattr(get_settings(), "kb_v2_enabled", False, raising=False)
    monkeypatch.setattr(
        "app.services.llm.complete_json", lambda p: {"questions": ["树脂体系选择", "固化机理研究", "盐雾失效模式"]}
    )
    hyde_calls: list[str] = []
    monkeypatch.setattr(
        "app.services.rag.hyde_expand",
        lambda q, domain=None: hyde_calls.append(q) or f"{q} + 假设答案",
    )
    _stub_search(monkeypatch)

    rg.retrieve_node({"topic": "主题", "query": "主题"}, mode="deep")
    assert len(hyde_calls) == 1


def test_hyde_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(get_settings(), "deep_subquestions", 1, raising=False)
    monkeypatch.setattr(get_settings(), "deep_hyde_enabled", False, raising=False)
    monkeypatch.setattr(get_settings(), "kg_enabled", False, raising=False)
    monkeypatch.setattr(get_settings(), "kb_v2_enabled", False, raising=False)
    hyde_calls: list[str] = []
    monkeypatch.setattr(
        "app.services.rag.hyde_expand", lambda q, domain=None: hyde_calls.append(q) or q
    )
    _stub_search(monkeypatch)

    rg.retrieve_node({"topic": "主题", "query": "主题"}, mode="deep")
    assert hyde_calls == []


def test_merged_pool_is_not_reranked_back_down(monkeypatch):
    """The cap that would have made decomposition pointless: reranking the merged
    pool to one query's worth throws the extra angles straight away."""
    monkeypatch.setattr(get_settings(), "deep_subquestions", 4, raising=False)
    monkeypatch.setattr(get_settings(), "deep_hyde_enabled", False, raising=False)
    monkeypatch.setattr(get_settings(), "deep_report_evidence_count", 24, raising=False)
    monkeypatch.setattr(get_settings(), "colbert_top_k", 4, raising=False)
    monkeypatch.setattr(get_settings(), "kg_enabled", False, raising=False)
    monkeypatch.setattr(get_settings(), "kb_v2_enabled", False, raising=False)
    monkeypatch.setattr(
        "app.services.llm.complete_json", lambda p: {"questions": ["树脂体系选择", "固化机理研究", "盐雾失效模式"]}
    )
    seen_k: list[int] = []

    class _Hit:
        def __init__(self, ev):
            self.evidence = ev

    monkeypatch.setattr(
        rg.colbert_store, "search",
        lambda q, settings=None, k=None: [_Hit(_ev(f"{q}-{i}")) for i in range(4)],
    )
    monkeypatch.setattr(rg.colbert_store, "bootstrap_seed_corpus", lambda s: None)
    monkeypatch.setattr(rg.colbert_store, "index_evidence", lambda *a, **k: 0)
    monkeypatch.setattr(
        rg, "llm_rerank",
        lambda q, ev, k=None: seen_k.append(k) or ev[: (k or len(ev))],
    )

    state = rg.retrieve_node({"topic": "主题", "query": "主题"}, mode="deep")
    assert seen_k == [24], "rerank must widen for a multi-angle pool"
    assert len(state["evidence"]) == 16  # 4 angles x 4 hits, all distinct


# ── evidence budget ──────────────────────────────────────────────────────────


def test_report_prompt_uses_the_configured_budget(monkeypatch):
    from app.services.deep_research.engine import _cross_validate_prompt

    monkeypatch.setattr(get_settings(), "deep_report_evidence_count", 3, raising=False)
    monkeypatch.setattr(get_settings(), "deep_report_snippet_chars", 20, raising=False)
    evidence = [
        Evidence(title=f"T{i}", snippet="甲" * 500, source="patents",
                 identifier=f"i{i}", relevance=0.8)
        for i in range(10)
    ]
    prompt = _cross_validate_prompt("主题", "综述", evidence)
    assert prompt.count("[patents]") == 3
    assert "甲" * 20 in prompt
    assert "甲" * 21 not in prompt


def test_report_budget_defaults_are_larger_than_the_old_hardcode():
    """12 x 300 chars was the single biggest reason the report was shallow."""
    s = get_settings()
    assert s.deep_report_evidence_count > 12
    assert s.deep_report_snippet_chars > 300


def test_grade_prompt_shares_the_budget(monkeypatch):
    """apply_grade can only mark documents the grader saw, so a hardcoded 12 here
    capped everything downstream — raising the report budget alone did nothing."""
    monkeypatch.setattr(get_settings(), "deep_report_evidence_count", 20, raising=False)
    evidence = [_ev(f"d{i}") for i in range(20)]
    prompt = rg._grade_prompt("主题", evidence)
    assert "[19]" in prompt


# ── the three misreporting bugs ───────────────────────────────────────────────


def test_deep_mode_does_not_emit_an_empty_recommend_stage(monkeypatch):
    """Recommendation happens inside generate_node in deep mode, so the trailing
    "recommend" stage was a progress bar with nothing behind it."""
    stages: list[str] = []
    monkeypatch.setattr(get_settings(), "deep_subquestions", 1, raising=False)
    monkeypatch.setattr(get_settings(), "deep_hyde_enabled", False, raising=False)
    monkeypatch.setattr(get_settings(), "kg_enabled", False, raising=False)
    monkeypatch.setattr(get_settings(), "kb_v2_enabled", False, raising=False)
    _stub_search(monkeypatch)
    monkeypatch.setattr(rg, "generate_node", lambda s, st=None: s)
    monkeypatch.setattr(rg, "_run_claim_check_loop", lambda s, st, cb: s)

    rg.run_research_graph(
        "主题", None, progress_cb=lambda st, msg, p=None: stages.append(st), mode="deep"
    )
    assert "recommend" not in stages


def test_claim_check_engine_reflects_the_verifier_that_ran(monkeypatch):
    """It was hardcoded "offline" at both report construction sites, so an
    LLM-verified report claimed to have been checked by token overlap."""
    from app.pipeline.claim_checker import ClaimCheckResult

    monkeypatch.setattr(
        rg, "check_claims", lambda *a, **k: ClaimCheckResult(engine="llm"), raising=False
    )
    import app.pipeline.claim_checker as cc

    monkeypatch.setattr(cc, "check_claims", lambda *a, **k: ClaimCheckResult(engine="llm"))
    state = rg.claim_check_node({"topic": "t", "report_markdown": "内容", "grounded_evidence": []})
    assert state["claim_check_engine"] == "llm"


def test_offline_verifier_still_reports_offline(monkeypatch):
    from app.pipeline.claim_checker import ClaimCheckResult
    import app.pipeline.claim_checker as cc

    monkeypatch.setattr(cc, "check_claims", lambda *a, **k: ClaimCheckResult(engine="offline"))
    state = rg.claim_check_node({"topic": "t", "report_markdown": "内容", "grounded_evidence": []})
    assert state["claim_check_engine"] == "offline"


def test_every_deep_stage_is_known_to_the_frontend():
    """The frontend's CRAG_STAGES must cover every stage deep mode emits, or
    stageIndex falls through to 0 and the progress bar jumps back to the first
    step. `regenerate` was missing, which is exactly what happened on a rewrite.

    Paired with a frontend test asserting the same list; both must be updated
    together when a stage is added.
    """
    expected = {"retrieve", "grade", "fallback", "generate", "claim_check", "regenerate"}
    import inspect

    src = inspect.getsource(rg)
    emitted = set(__import__("re").findall(r'_emit\(progress_cb, "([a-z_]+)"', src))
    # "recommend" is emitted only by recommend mode, which does not render the bar.
    assert emitted - {"recommend"} <= expected, f"unknown deep stage: {emitted - expected}"

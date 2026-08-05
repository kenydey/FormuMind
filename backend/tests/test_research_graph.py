"""Tests for CRAG research graph routing."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.config import Settings
from app.domain.schemas import Evidence, ProductDomain, Requirement
from app.pipeline.research_graph import (
    DocGrade,
    GradeResult,
    GradeVerdict,
    apply_grade,
    build_langgraph,
    claim_check_node,
    grade_evidence,
    route_after_claim_check,
    route_after_grade,
    run_research_graph,
)


@pytest.fixture
def settings(tmp_path):
    return Settings(colbert_index_dir=str(tmp_path / "colbert"))


def test_route_after_grade_to_fallback():
    state = {"grade": GradeVerdict.incorrect, "fallback_used": False}
    assert route_after_grade(state) == "fallback"


def test_route_after_grade_to_generate():
    state = {"grade": GradeVerdict.correct, "fallback_used": False}
    assert route_after_grade(state) == "generate"


def test_apply_grade_filters_by_doc_grades():
    evidence = [
        Evidence(source="a", identifier="1", title="A", snippet="alpha", relevance=0.5),
        Evidence(source="b", identifier="2", title="B", snippet="beta", relevance=0.5),
    ]
    grade = GradeResult(
        verdict=GradeVerdict.correct,
        doc_grades=[DocGrade(index=0, relevant=True, score=0.9)],
    )
    grounded = apply_grade(evidence, grade, Settings(colbert_min_score=0.3))
    assert len(grounded) == 1
    assert grounded[0].identifier == "1"


def test_run_research_graph_offline(settings):
    req = Requirement(domain=ProductDomain.anticorrosion_coating, salt_spray_hours=500)
    stages: list[str] = []

    with patch("app.pipeline.research_graph.FederatedSearchEngine") as Fed:
        Fed.return_value.search.return_value.evidence = []
        state = run_research_graph(
            "epoxy corrosion primer",
            req=req,
            settings=settings,
            progress_cb=lambda stage, _msg, _partial=None: stages.append(stage),
        )

    assert "retrieve" in stages
    assert "grade" in stages
    assert "generate" in stages
    assert "claim_check" in stages
    assert state.get("grounded_evidence") is not None
    assert "verified_claims" in state


def test_grade_evidence_offline_heuristic(settings):
    evidence = [
        Evidence(source="seed", identifier="x", title="t", snippet="s", relevance=0.9)
        for _ in range(3)
    ]
    grade = grade_evidence("topic", evidence, settings)
    assert grade.verdict == GradeVerdict.correct


def test_route_after_claim_check_to_regenerate():
    state = {"claim_check_passed": False, "claim_check_attempts": 1}
    assert route_after_claim_check(state) == "regenerate"


def test_route_after_claim_check_to_end_when_passed():
    state = {"claim_check_passed": True, "claim_check_attempts": 1}
    assert route_after_claim_check(state) == "end"


def test_claim_check_node_offline(settings):
    state = {
        "topic": "epoxy primer",
        "report_markdown": (
            "## 关键发现\n\n"
            "- Waterborne epoxy with zinc phosphate inhibitor improves corrosion resistance.\n"
        ),
        "grounded_evidence": [
            Evidence(
                source="seed",
                identifier="1",
                title="Zinc phosphate epoxy",
                snippet="Waterborne epoxy with zinc phosphate inhibitor.",
                relevance=0.9,
            )
        ],
    }
    out = claim_check_node(state, settings)
    assert out["stage"] == "claim_check"
    assert out.get("verified_claims")


def test_build_langgraph_invoke_offline(settings):
    compiled = build_langgraph(settings)
    if compiled is None:
        pytest.skip("langgraph not installed")
    req = Requirement(domain=ProductDomain.anticorrosion_coating, salt_spray_hours=500)
    with patch("app.pipeline.research_graph.FederatedSearchEngine") as Fed:
        Fed.return_value.search.return_value.evidence = []
        result = compiled.invoke(
            {
                "topic": "epoxy corrosion primer",
                "query": "epoxy corrosion primer",
                "req": req,
                "pre_index": [],
                "fallback_used": False,
            }
        )
    assert result.get("stage") in ("claim_check", "regenerate")
    assert result.get("verified_claims") is not None


# ── CRAG degradation: the grader and the fallback under failure ──────────────
#
# These run offline by design and must NOT be marked golden_eval — that marker
# means "slow QA-dataset suite" and is deselected by CI (`-m "not golden_eval"`),
# which would leave the degradation paths untested where it matters most.
#
# The LLM is faked by monkeypatching `app.services.llm.complete_json`, the
# convention already used in test_deep_research_depth.py:61,69. Intercepting
# HTTP instead (respx) would pin the tests to whichever provider base_url is
# configured, and could not touch the search path at all — `ddgs` is a library
# call, not an httpx request.


def _evidence(n: int, relevance: float) -> list[Evidence]:
    return [
        Evidence(
            source="seed",
            identifier=f"e{i}",
            title=f"t{i}",
            snippet="锌系磷化膜耐蚀性",
            relevance=relevance,
        )
        for i in range(n)
    ]


def test_grade_falls_back_when_the_llm_raises(monkeypatch, settings):
    """A provider outage must degrade the grade, not abort the research run."""

    def boom(prompt):
        raise RuntimeError("provider returned 429")

    monkeypatch.setattr("app.services.llm.complete_json", boom)

    grade = grade_evidence("水性环氧防腐涂料", _evidence(3, 0.9), settings)

    assert isinstance(grade, GradeResult)
    assert grade.verdict in (GradeVerdict.correct, GradeVerdict.incorrect)
    # The field is `reason`; `grade_reason` belongs to the outer result model.
    assert "offline heuristic" in grade.reason


@pytest.mark.parametrize(
    "payload",
    [
        None,                      # provider unreachable / empty completion
        "not json at all",         # prose instead of an object
        {},                        # object with nothing in it
        {"verdict": "banana"},     # a verdict outside the enum
        {"doc_grades": []},        # right shape, no verdict
    ],
    ids=["none", "prose", "empty", "bad-verdict", "no-verdict"],
)
def test_grade_falls_back_on_malformed_llm_output(monkeypatch, settings, payload):
    """Anything the parser cannot trust falls through to the heuristic.

    A model that answers in prose, or invents a verdict, is far more common
    than one that errors outright, and it must not reach the caller as a
    half-parsed grade.
    """
    monkeypatch.setattr("app.services.llm.complete_json", lambda prompt: payload)

    grade = grade_evidence("水性环氧防腐涂料", _evidence(3, 0.9), settings)

    assert grade.verdict in (GradeVerdict.correct, GradeVerdict.incorrect)
    assert "offline heuristic" in grade.reason


def test_grade_says_incorrect_without_evidence(settings):
    """The empty case is distinct from a failed grade and keeps its own reason."""
    grade = grade_evidence("主题", [], settings)
    assert grade.verdict == GradeVerdict.incorrect
    assert "offline heuristic" not in grade.reason


def test_graph_survives_a_failing_federated_search(monkeypatch, settings):
    """The fallback is itself a network call, and it can fail too.

    CRAG reaches the fallback precisely when local retrieval was judged
    insufficient, so a search outage lands on the path that was already the
    last resort — and it used to propagate, turning "the web search is down"
    into a failed research run.

    Note `pre_index=None`: `_needs_fallback` short-circuits in recommend mode
    when pre-loaded sources exist, so passing any would skip the very node
    under test and the assertion would hold vacuously.
    """
    from app.services import literature

    called: list[int] = []

    def boom(*args, **kwargs):
        called.append(1)
        raise RuntimeError("upstream search returned 502")

    monkeypatch.setattr(literature, "iter_search", boom)
    monkeypatch.setattr("app.services.llm.complete_json", lambda prompt: None)

    state = run_research_graph(
        topic="水性环氧防腐涂料",
        req=Requirement(domain=ProductDomain.anticorrosion_coating),
        query="水性环氧防腐涂料",
        pre_index=None,
        mode="recommend",
        settings=settings,
    )

    assert called, "the fallback never ran — this test would prove nothing"
    assert state.get("fallback_used") is True
    assert isinstance(state, dict) and state.get("stage")

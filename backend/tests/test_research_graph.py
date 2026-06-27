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

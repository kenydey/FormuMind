"""Tests for evidence reviewer fix-loop."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import reviewer_fix_loop as rfl


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(rfl, "_data_root", lambda: tmp_path)
    return tmp_path


class _Settings:
    evidence_reviewer_fix_loop_enabled = True
    evidence_reviewer_enabled = True


def test_fix_loop_disabled_returns_none(tmp_data):
    class Off:
        evidence_reviewer_fix_loop_enabled = False

    ans, meta = rfl.run_fix_loop(
        question="q",
        answer="a",
        citations=[],
        review={"status": "failure", "notes": ["x"]},
        settings=Off(),
        repair_fn=lambda q, a: "fixed",
    )
    assert ans == "a"
    assert meta is None


def test_fix_loop_one_round_repair(tmp_data, monkeypatch):
    calls = {"n": 0}

    def fake_review(question, answer, citations, *, settings):
        if "fixed" in answer:
            return {"status": "pass", "notes": [], "suggestion": None}
        return {
            "status": "failure",
            "notes": ["1 条断言无据"],
            "suggestion": "收紧",
            "unsupported_count": 1,
            "weak_count": 0,
        }

    monkeypatch.setattr(
        "app.services.evidence_reviewer.review_answer",
        fake_review,
    )

    def repair(q, auditor):
        calls["n"] += 1
        assert "[Auditor]" in auditor
        return "fixed answer with [^1]"

    ans, meta = rfl.run_fix_loop(
        question="What temp?",
        answer="bad claim",
        citations=[],
        review={
            "status": "failure",
            "notes": ["1 条断言无据"],
            "suggestion": "收紧",
        },
        settings=_Settings(),
        repair_fn=repair,
        max_rounds=3,
    )
    assert calls["n"] == 1
    assert "fixed" in ans
    assert meta is not None
    assert meta["rounds"] == 1
    assert meta["status"] == "pass"


def test_fix_loop_unaddressed_after_max(tmp_data, monkeypatch):
    monkeypatch.setattr(
        "app.services.evidence_reviewer.review_answer",
        lambda *a, **k: {
            "status": "failure",
            "notes": ["still bad"],
            "suggestion": "x",
        },
    )
    n = {"i": 0}

    def repair(q, auditor):
        n["i"] += 1
        return f"attempt {n['i']}"

    ans, meta = rfl.run_fix_loop(
        question="q",
        answer="a0",
        citations=[],
        review={"status": "failure", "notes": ["still bad"], "suggestion": "x"},
        settings=_Settings(),
        repair_fn=repair,
        max_rounds=2,
        project_id="proj-x",
    )
    assert meta is not None
    assert meta["rounds"] == 2
    assert meta.get("unaddressed")
    soft = tmp_data / "preflight" / "proj-x" / "evidence_soft.json"
    assert soft.is_file()

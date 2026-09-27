"""Tests for W5-1 (P2-1): query-aware evidence compression, tier 1.

Covers: budget truncation, query-relevant ranking, per-item degradation
(full -> short snippet -> metadata only), same-source dedup, fail-open,
no input mutation, and the feature-flag helpers.
"""
from __future__ import annotations

import pytest

from app.domain.schemas import Evidence
from app.services import query_aware_compression as qac


def make_ev(
    identifier: str,
    snippet: str,
    *,
    title: str = "Test paper",
    relevance: float = 0.5,
    **kwargs,
) -> Evidence:
    return Evidence(
        source="literature",
        identifier=identifier,
        title=title,
        snippet=snippet,
        relevance=relevance,
        **kwargs,
    )


def total_tokens(items: list[Evidence]) -> int:
    return sum(qac.estimate_tokens(qac._full_text(ev)) for ev in items)


def test_budget_truncation_respected():
    srcs = [make_ev(f"p{i}", "x " * 2000, relevance=0.5) for i in range(6)]
    out = qac.compress_evidence("coating formulation", srcs, token_budget=1500)
    assert out, "must keep at least one item"
    assert total_tokens(out) <= 1500
    # Budget too small for 6 full passages: later items must be degraded,
    # not silently kept at full length.
    assert any(len(ev.snippet) < 4000 for ev in out[1:])


def test_query_relevant_evidence_ranked_first():
    relevant = make_ev(
        "rel",
        "waterborne coating corrosion resistance formulation with zinc phosphate",
        relevance=0.1,
    )
    irrelevant = make_ev(
        "irr",
        "quantum dot solar cell efficiency under low light conditions",
        relevance=0.9,
    )
    # Budget fits exactly one full item (20 tokens): the query-relevant one
    # must be kept at full length while the irrelevant one is dropped.
    out = qac.compress_evidence(
        "waterborne coating corrosion resistance", [irrelevant, relevant], token_budget=21
    )
    assert [ev.identifier for ev in out] == ["rel"]
    assert "waterborne" in out[0].snippet  # full form, not degraded


def test_degradation_short_snippet_keeps_citation():
    long_snippet = "corrosion inhibitor data. " * 500  # ~12k chars
    ev = make_ev("big", long_snippet, title="Important paper", relevance=0.9)
    out = qac.compress_evidence("corrosion inhibitor", [ev], token_budget=300)
    assert len(out) == 1
    got = out[0]
    assert got.identifier == "big"
    assert got.title == "Important paper"
    assert len(got.snippet) <= qac.SHORT_SNIPPET_CHARS + 2  # + ellipsis
    assert total_tokens(out) <= 300


def test_degradation_metadata_only_on_tiny_budget():
    ev = make_ev("big", "y " * 3000, title="Important paper", relevance=0.9)
    out = qac.compress_evidence("paper", [ev], token_budget=8)
    assert len(out) == 1
    assert out[0].snippet == ""
    assert out[0].title == "Important paper"
    assert out[0].identifier == "big"


def test_same_source_dedup_and_cap():
    dup = "identical passage about epoxy curing. " * 50
    srcs = [make_ev("same-id", dup, relevance=0.9 - i * 0.01) for i in range(5)]
    srcs.append(make_ev("other-id", "different content here", relevance=0.1))
    out = qac.compress_evidence("epoxy curing", srcs, token_budget=20000)
    same = [ev for ev in out if ev.identifier == "same-id"]
    assert len(same) <= qac.MAX_PER_SOURCE
    assert len(same) >= 1
    assert any(ev.identifier == "other-id" for ev in out)


def test_fail_open_returns_original_sources(monkeypatch):
    srcs = [make_ev("a", "text one"), make_ev("b", "text two")]

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(qac, "_compress_evidence", _boom)
    out = qac.compress_evidence("question", srcs, token_budget=100)
    assert out == srcs
    assert out is not srcs  # a copy, not the same list object


def test_inputs_not_mutated():
    srcs = [make_ev("a", "z " * 2000, relevance=0.9)]
    original_snippet = srcs[0].snippet
    qac.compress_evidence("z", srcs, token_budget=100)
    assert srcs[0].snippet == original_snippet


def test_empty_inputs():
    assert qac.compress_evidence("q", [], token_budget=100) == []
    # Empty question must not crash; ranking falls back to relevance/age.
    srcs = [make_ev("a", "some text", relevance=0.7)]
    out = qac.compress_evidence("", srcs, token_budget=10000)
    assert [ev.identifier for ev in out] == ["a"]


def test_zero_budget_fails_open():
    srcs = [make_ev("a", "text")]
    assert qac.compress_evidence("q", srcs, token_budget=0) == srcs


def test_age_signal_does_not_crash():
    ev = make_ev("old", "highly cited old paper text", relevance=0.5,
                 cited_by=500, pub_year=2001)
    ev2 = make_ev("new", "brand new paper text", relevance=0.5,
                  cited_by=3, pub_year=2026)
    out = qac.compress_evidence("paper", [ev, ev2], token_budget=20000)
    assert len(out) == 2


class _StubSettings:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_flag_helpers_defaults():
    assert qac.query_compress_enabled(_StubSettings()) is True
    assert qac.query_compress_token_budget(_StubSettings()) == qac.DEFAULT_TOKEN_BUDGET


def test_flag_helpers_override():
    s = _StubSettings(query_compress_enabled=False, query_compress_token_budget=500)
    assert qac.query_compress_enabled(s) is False
    assert qac.query_compress_token_budget(s) == 500


def test_flag_helpers_bad_budget_falls_back():
    s = _StubSettings(query_compress_token_budget="junk")
    assert qac.query_compress_token_budget(s) == qac.DEFAULT_TOKEN_BUDGET
    s2 = _StubSettings(query_compress_token_budget=-5)
    assert qac.query_compress_token_budget(s2) == qac.DEFAULT_TOKEN_BUDGET

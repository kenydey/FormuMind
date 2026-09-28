"""Wave 3-2: tier-2 evidence compression observability.

Covers: in-process counters (attempts/triggers/llm_calls/token economics),
structured logging with query hash (never raw question text), fail-open
counting on LLM errors, and the GET /api/ops/evidence-stats shape.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

from app.domain.schemas import Evidence
from app.services import query_aware_compression as qac

LONG = "zinc phosphate corrosion inhibition in waterborne coating. " * 60
SHORT = "short note"


def make_ev(identifier: str, snippet: str) -> Evidence:
    return Evidence(
        source="literature",
        identifier=identifier,
        title="Test paper",
        snippet=snippet,
        relevance=0.5,
    )


def make_settings(**kwargs) -> SimpleNamespace:
    base = {
        "query_compress_llm_enabled": True,
        "evidence_reviewer_model": "small-model",
        "query_compress_token_budget": 12000,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def fake_llm_ok(prompt, **kwargs):
    return {
        "summaries": [
            {"id": "long1", "summary": "summary one."},
            {"id": "long2", "summary": "summary two."},
        ]
    }


def setup_function(_):
    qac.reset_evidence_stats()


def test_trigger_counts_and_token_economics(caplog):
    srcs = [make_ev("long1", LONG), make_ev("long2", LONG)]
    with patch("app.services.llm.complete_json", side_effect=fake_llm_ok):
        with caplog.at_level(logging.INFO, logger="app.services.query_aware_compression"):
            qac.llm_compress_evidence("secret question text", srcs, settings=make_settings())
    stats = qac.get_evidence_stats()
    assert stats["tier2_attempts"] == 1
    assert stats["tier2_triggers"] == 1
    assert stats["llm_calls"] == 1
    assert stats["tokens_before"] > stats["tokens_after"] > 0
    # structured log: query hash present, raw question absent
    lines = [r.getMessage() for r in caplog.records]
    assert any("tier2 evidence compression" in m and "triggered=true" in m for m in lines)
    assert not any("secret question text" in m for m in lines)


def test_no_long_items_counts_attempt_only(caplog):
    srcs = [make_ev("s1", SHORT)]
    with patch("app.services.llm.complete_json") as mock_call:
        with caplog.at_level(logging.INFO, logger="app.services.query_aware_compression"):
            out = qac.llm_compress_evidence("q", srcs, settings=make_settings())
    assert mock_call.call_count == 0
    assert out == srcs or [e.identifier for e in out] == ["s1"]
    stats = qac.get_evidence_stats()
    assert stats["tier2_attempts"] == 1
    assert stats["tier2_triggers"] == 0
    assert stats["llm_calls"] == 0
    lines = [r.getMessage() for r in caplog.records]
    assert any("triggered=false" in m for m in lines)


def test_llm_error_counts_attempt_fail_open():
    srcs = [make_ev("long1", LONG)]
    with patch("app.services.llm.complete_json", side_effect=RuntimeError("boom")):
        out = qac.llm_compress_evidence("q", srcs, settings=make_settings())
    assert len(out) == 1  # fail-open fallback
    stats = qac.get_evidence_stats()
    assert stats["tier2_attempts"] == 1
    assert stats["tier2_triggers"] == 0


def test_ops_endpoint_shape():
    from app.api.ops import evidence_stats

    qac._bump_stats(tier2_attempts=4, tier2_triggers=1, llm_calls=1,
                    tokens_before=1000, tokens_after=100)
    body = evidence_stats()
    assert body["tier2_attempts"] == 4
    assert body["tier2_triggers"] == 1
    assert body["tokens_saved"] == 900
    assert body["trigger_rate"] == 0.25
    assert body["process_local"] is True


def test_ops_endpoint_empty_is_zeroed():
    from app.api.ops import evidence_stats

    body = evidence_stats()
    assert body["trigger_rate"] == 0.0
    assert body["tokens_saved"] == 0


def test_stats_snapshot_is_copy():
    qac._bump_stats(tier2_attempts=1)
    snap = qac.get_evidence_stats()
    snap["tier2_attempts"] = 999
    assert qac.get_evidence_stats()["tier2_attempts"] == 1

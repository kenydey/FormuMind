"""Tests for W5-1 (P2-1): query-aware evidence compression, tier 2 (LLM rewrite).

Covers: citation-anchor preservation, short-text skip (no LLM call),
fail-open fallback to tier 1 on LLM errors, flag default off, model
override passthrough (W5-2), and ignoring bogus LLM output.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.config import Settings
from app.domain.schemas import Evidence
from app.services import query_aware_compression as qac


def make_ev(
    identifier: str,
    snippet: str,
    *,
    title: str = "Test paper",
    relevance: float = 0.5,
    page: int | None = 7,
    paragraph: int | None = 3,
    url: str | None = "https://example.com/p1",
) -> Evidence:
    return Evidence(
        source="literature",
        identifier=identifier,
        title=title,
        snippet=snippet,
        relevance=relevance,
        page=page,
        paragraph=paragraph,
        url=url,
    )


def make_settings(**kwargs) -> SimpleNamespace:
    base = {
        "query_compress_llm_enabled": True,
        "evidence_reviewer_model": "small-model",
        "query_compress_token_budget": 12000,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


LONG = "corrosion inhibition mechanism of zinc phosphate in waterborne coating. " * 60


def fake_llm_ok(prompt, **kwargs):
    return {
        "summaries": [
            {"id": "long1", "summary": "磷酸锌通过缓蚀机理提升水性涂层耐蚀性。"},
            {"id": "long2", "summary": "涂层附着力测试显示 5B 等级。"},
        ]
    }


def test_llm_flag_defaults_off():
    assert Settings.model_fields["query_compress_llm_enabled"].default is False
    # Helper: missing attribute (old settings objects) also reads as off.
    assert qac.query_compress_llm_enabled(SimpleNamespace()) is False
    assert qac.query_compress_llm_enabled(make_settings()) is True


def test_summary_preserves_citation_anchor():
    srcs = [make_ev("long1", LONG), make_ev("long2", LONG, page=12)]
    with patch(
        "app.services.llm.complete_json", side_effect=fake_llm_ok
    ) as mock_call:
        out = qac.llm_compress_evidence("水性涂层耐蚀性", srcs, settings=make_settings())
    by_id = {ev.identifier: ev for ev in out}
    for sid in ("long1", "long2"):
        ev = by_id[sid]
        # Citation anchors untouched.
        assert ev.identifier == sid
        assert ev.title == "Test paper"
        assert ev.url == "https://example.com/p1"
        # Only the snippet is replaced, with a visible marker.
        assert ev.snippet.startswith(qac.LLM_SUMMARY_PREFIX)
        assert "磷酸锌" in by_id["long1"].snippet
    assert by_id["long1"].page == 7
    assert by_id["long1"].paragraph == 3
    assert by_id["long2"].page == 12
    # Small model was used for compression (W5-2 passthrough).
    assert mock_call.call_args.kwargs.get("model") == "small-model"


def test_short_texts_skip_llm_entirely():
    srcs = [make_ev("s1", "short snippet one"), make_ev("s2", "short snippet two")]
    with patch("app.services.llm.complete_json") as mock_call:
        out = qac.llm_compress_evidence("anything", srcs, settings=make_settings())
    mock_call.assert_not_called()
    assert [ev.identifier for ev in out] == ["s1", "s2"]
    assert out[0].snippet == "short snippet one"  # untouched


def test_mixed_lengths_only_long_summarized():
    srcs = [make_ev("long1", LONG), make_ev("short1", "brief note")]
    with patch("app.services.llm.complete_json", side_effect=fake_llm_ok):
        out = qac.llm_compress_evidence("耐蚀性", srcs, settings=make_settings())
    by_id = {ev.identifier: ev for ev in out}
    assert by_id["long1"].snippet.startswith(qac.LLM_SUMMARY_PREFIX)
    assert by_id["short1"].snippet == "brief note"


def test_llm_failure_falls_back_to_tier1():
    srcs = [make_ev(f"p{i}", LONG, relevance=0.1 * i) for i in range(4)]
    settings = make_settings()
    expected = qac.compress_evidence(
        "耐蚀性", srcs, token_budget=qac.query_compress_token_budget(settings)
    )
    with patch("app.services.llm.complete_json", side_effect=RuntimeError("boom")):
        out = qac.llm_compress_evidence("耐蚀性", srcs, settings=settings)
    assert [ev.identifier for ev in out] == [ev.identifier for ev in expected]
    assert [ev.snippet for ev in out] == [ev.snippet for ev in expected]


def test_llm_none_reply_falls_back_to_tier1():
    srcs = [make_ev("long1", LONG)]
    settings = make_settings()
    expected = qac.compress_evidence(
        "耐蚀性", srcs, token_budget=qac.query_compress_token_budget(settings)
    )
    with patch("app.services.llm.complete_json", return_value=None):
        out = qac.llm_compress_evidence("耐蚀性", srcs, settings=settings)
    assert [ev.identifier for ev in out] == [ev.identifier for ev in expected]


def test_bogus_llm_output_ignored():
    def fake_bogus(prompt, **kwargs):
        return {
            "summaries": [
                {"id": "no-such-id", "summary": "hallucinated evidence"},
                {"id": "long1", "summary": "   "},  # empty → ignored
                {"id": "long1", "summary": "real summary kept"},
            ]
        }

    srcs = [make_ev("long1", LONG)]
    settings = make_settings()
    expected = qac.compress_evidence(
        "耐蚀性", srcs, token_budget=qac.query_compress_token_budget(settings)
    )
    with patch("app.services.llm.complete_json", side_effect=fake_bogus):
        out = qac.llm_compress_evidence("耐蚀性", srcs, settings=settings)
    # No usable summaries (unknown id + empty) → tier-1 fallback.
    assert [ev.identifier for ev in out] == [ev.identifier for ev in expected]
    assert not any(ev.identifier == "no-such-id" for ev in out)


def test_empty_sources():
    with patch("app.services.llm.complete_json") as mock_call:
        assert qac.llm_compress_evidence("q", [], settings=make_settings()) == []
    mock_call.assert_not_called()


def test_input_not_mutated():
    srcs = [make_ev("long1", LONG)]
    original_snippet = srcs[0].snippet
    with patch("app.services.llm.complete_json", side_effect=fake_llm_ok):
        qac.llm_compress_evidence("耐蚀性", srcs, settings=make_settings())
    assert srcs[0].snippet == original_snippet

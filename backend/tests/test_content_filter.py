"""KB P0 tests — search-result quality filter (rules + LLM judge)."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.domain.schemas import Evidence
from app.services import content_filter


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _ev(identifier: str, title: str = "Epoxy coating study", snippet: str = "", **kw) -> Evidence:
    return Evidence(
        source=kw.pop("source", "internet"),
        identifier=identifier,
        title=title,
        snippet=snippet or "Detailed anticorrosion epoxy formulation with zinc phosphate inhibitor data.",
        relevance=kw.pop("relevance", 0.8),
        **kw,
    )


# ── rule tier ────────────────────────────────────────────────────────────────


def test_disabled_flag_is_noop(monkeypatch):
    monkeypatch.setenv("FORMUMIND_CONTENT_FILTER_ENABLED", "false")
    get_settings.cache_clear()
    junk = _ev("https://www.pinterest.com/pin/123", snippet="x")
    kept, report = content_filter.filter_evidence([junk])
    assert kept == [junk]
    assert report.dropped == 0


def test_blocked_domain_dropped():
    rows = [
        _ev("https://www.pinterest.com/pin/123"),
        _ev("https://patents.example.org/EP123", title="EP patent on epoxy primers"),
    ]
    kept, report = content_filter.filter_evidence(rows)
    assert len(kept) == 1
    assert kept[0].identifier.startswith("https://patents.example.org")
    assert report.dropped_by_reason.get("blocked_domain") == 1


def test_blocked_domain_subdomain_matches():
    kept, _ = content_filter.filter_evidence([_ev("https://shop.alibaba.com/item/9")])
    assert kept == []


def test_custom_blocklist_from_env(monkeypatch):
    monkeypatch.setenv("FORMUMIND_CONTENT_FILTER_BLOCKED_DOMAINS", '["junk.example"]')
    get_settings.cache_clear()
    kept, _ = content_filter.filter_evidence([_ev("https://junk.example/page")])
    assert kept == []


def test_garbage_snippet_dropped():
    rows = [
        _ev("https://a.example/1", title="x", snippet="ok"),  # too short
        _ev("https://b.example/2", title="....", snippet="###$$$%%%^^^&&&***((()))___+++===[[[]]]"),
        _ev("https://c.example/3"),  # healthy
    ]
    kept, report = content_filter.filter_evidence(rows)
    assert [e.identifier for e in kept] == ["https://c.example/3"]
    assert report.dropped_by_reason.get("garbage_snippet") == 2


def test_patent_identifiers_not_treated_as_domains():
    """Non-URL identifiers (patent numbers, DOIs) never hit the domain rule."""
    rows = [
        _ev("US1234567", source="USPTO", title="Chromate-free conversion coating"),
        _ev("10.1016/j.porgcoat.2020.105678", source="OpenAlex"),
    ]
    kept, report = content_filter.filter_evidence(rows)
    assert len(kept) == 2
    assert report.dropped == 0


def test_seed_corpus_always_passes():
    seed = Evidence(
        source="seed", identifier="seed-1", title="s", snippet="x",
        relevance=0.5, is_seed_corpus=True,
    )
    kept, report = content_filter.filter_evidence([seed])
    assert kept == [seed]
    assert report.dropped == 0


def test_near_duplicate_collapsed_keeps_first():
    text = "Waterborne epoxy zinc-rich primer achieves 720h salt spray on carbon steel substrates."
    a = _ev("https://a.example/orig", snippet=text, relevance=0.9)
    b = _ev("https://b.example/mirror", snippet=text + " (via mirror)", relevance=0.5)
    kept, report = content_filter.filter_evidence([a, b])
    assert kept == [a]
    assert report.dropped_by_reason.get("near_duplicate") == 1


def test_distinct_content_not_deduped():
    a = _ev("https://a.example/1", snippet="Polyurethane topcoat weathering resistance QUV 2000h test data and gloss retention.")
    b = _ev("https://b.example/2", snippet="Alkaline degreaser formulation with sodium metasilicate for aluminum cleaning at 60C.")
    kept, _ = content_filter.filter_evidence([a, b])
    assert len(kept) == 2


def test_merge_filter_rank_integration():
    """The rule tier is live inside literature's convergence point."""
    from app.services.literature import _merge_filter_rank

    rows = [
        _ev("https://www.pinterest.com/pin/1", title="epoxy coating pin",
            snippet="epoxy coating salt spray corrosion data table"),
        _ev("https://journal.example/paper", title="epoxy coating salt spray study",
            snippet="epoxy coating salt spray corrosion resistance study on steel"),
    ]
    ranked, _ = _merge_filter_rank(rows, "epoxy coating salt spray", 10)
    ids = [e.identifier for e in ranked]
    assert "https://journal.example/paper" in ids
    assert "https://www.pinterest.com/pin/1" not in ids


# ── LLM judge tier ───────────────────────────────────────────────────────────


def _enable_judge(monkeypatch):
    monkeypatch.setenv("FORMUMIND_CONTENT_FILTER_LLM_JUDGE", "true")
    get_settings.cache_clear()
    settings = get_settings()
    monkeypatch.setattr(type(settings), "get_active_api_key", lambda self: "sk-test")


def test_llm_judge_off_by_default():
    rows = [_ev("https://a.example/1"), _ev("https://b.example/2")]
    kept, report = content_filter.llm_quality_judge(rows, "epoxy")
    assert kept == rows
    assert report.dropped == 0


def test_llm_judge_drops_flagged_items(monkeypatch):
    _enable_judge(monkeypatch)
    monkeypatch.setattr("app.services.llm.complete_json", lambda prompt: {"drop": [1]})
    rows = [
        _ev("https://a.example/1"),
        _ev("https://b.example/2", snippet="Buy cheap paint online free shipping discount sale."),
        _ev("https://c.example/3"),
    ]
    kept, report = content_filter.llm_quality_judge(rows, "epoxy")
    assert [e.identifier for e in kept] == ["https://a.example/1", "https://c.example/3"]
    assert report.dropped_by_reason.get("llm_judge") == 1


def test_llm_judge_ignores_overzealous_drops(monkeypatch):
    _enable_judge(monkeypatch)
    monkeypatch.setattr("app.services.llm.complete_json", lambda prompt: {"drop": [0, 1, 2]})
    rows = [_ev(f"https://x.example/{i}") for i in range(4)]
    kept, _ = content_filter.llm_quality_judge(rows, "epoxy")
    assert kept == rows  # >50% drops rejected as suspect


def test_llm_judge_keeps_all_on_failure(monkeypatch):
    _enable_judge(monkeypatch)

    def boom(prompt):
        raise RuntimeError("llm down")

    monkeypatch.setattr("app.services.llm.complete_json", boom)
    rows = [_ev("https://a.example/1"), _ev("https://b.example/2")]
    kept, _ = content_filter.llm_quality_judge(rows, "epoxy")
    assert kept == rows

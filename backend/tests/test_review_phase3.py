"""Regression tests for review phase-3 fixes (retrieval & LLM pipeline)."""
from __future__ import annotations

import asyncio

import pytest

from app.services import literature, llm
from app.services.deep_research.engine import DeepResearchEngine
from app.services.deep_research.query_expander import SearchQueries
from app.services.deep_research.models import ExpandedQuery


def _stub_queries() -> SearchQueries:
    expanded = ExpandedQuery(
        intent="test",
        chinese_keywords=["防腐涂料"],
        english_synonyms=["anticorrosion coating"],
        ipc_cpc_suggestions=["C09D"],
    )
    return SearchQueries(
        expanded=expanded,
        rank_q="anticorrosion coating C09D",
        patent_q="anticorrosion coating",
        western_q="anticorrosion coating",
        chinese_q="防腐涂料",
        ipc_codes=("C09D",),
    )


def test_iter_search_skips_expansion_when_queries_given(monkeypatch):
    """Regression: pre-built SearchQueries must not trigger a second expansion."""
    calls = {"n": 0}

    def spy_prepare(query):
        calls["n"] += 1
        return _stub_queries()

    monkeypatch.setattr(literature, "_prepare_search_queries", spy_prepare)
    literature.iter_search(
        "anticorrosion coating",
        ["literature"],
        total_limit=5,
        per_source_cap=5,
        max_rounds=1,
        queries=_stub_queries(),
    )
    assert calls["n"] == 0


def test_deep_research_retrieve_expands_exactly_once(monkeypatch):
    """Regression: retrieve() previously ran query expansion 2-3 times."""
    from app.services.deep_research import query_expander

    calls = {"n": 0}
    real_offline = query_expander.QueryExpander._offline_expand

    def spy_expand(self, user_query):
        calls["n"] += 1
        return real_offline(self, user_query)

    monkeypatch.setattr(query_expander.QueryExpander, "expand", spy_expand)
    engine = DeepResearchEngine()
    evidence, expanded = engine.retrieve(
        "anticorrosion coating", source_types=["literature"], total_limit=5, per_source_cap=5
    )
    assert calls["n"] == 1
    assert expanded.intent


def test_complete_json_parses_json_after_prose_fence(monkeypatch):
    """Regression: replies with a prose fence before the JSON fence must parse."""
    reply = (
        "Here is some explanation first:\n"
        "```text\nthinking out loud, not JSON\n```\n"
        "And the result:\n"
        '```json\n{"intent": "ok", "keywords": ["a"]}\n```\n'
    )
    monkeypatch.setattr(llm, "_call_llm", lambda prompt: reply)
    data = llm.complete_json("prompt")
    assert data == {"intent": "ok", "keywords": ["a"]}


def test_complete_json_parses_bare_object_with_surrounding_prose(monkeypatch):
    reply = 'Sure! {"a": 1} hope that helps.'
    monkeypatch.setattr(llm, "_call_llm", lambda prompt: reply)
    assert llm.complete_json("prompt") == {"a": 1}


def test_complete_json_returns_none_for_garbage(monkeypatch):
    monkeypatch.setattr(llm, "_call_llm", lambda prompt: "not json at all")
    assert llm.complete_json("prompt") is None


def test_sse_poll_fallback_emits_terminal_timeout_frame():
    """Regression: the poll fallback previously closed the stream silently."""
    from app.api.tasks import _poll_until_terminal
    from app.worker.task_progress import TaskProgressStatus

    async def collect():
        events = []
        async for ev in _poll_until_terminal("no-such-task", timeout_s=0.3):
            events.append(ev)
        return events

    events = asyncio.run(collect())
    assert events, "stream must emit a terminal frame on timeout"
    last = events[-1]
    assert last.status == TaskProgressStatus.FAILED
    assert last.stage == "stream_timeout"


def test_pdf_download_flag_enriches_evidence(monkeypatch):
    """Regression: FORMUMIND_PDF_DOWNLOAD flag was dead — never invoked."""
    from app.config import get_settings

    monkeypatch.setenv("FORMUMIND_PDF_DOWNLOAD", "true")
    get_settings.cache_clear()
    try:
        called = {"n": 0}

        def spy_enrich(evidence, max_pdfs=3, timeout=20.0):
            called["n"] += 1
            return evidence

        import app.services.pdf_downloader as pdfd

        monkeypatch.setattr(pdfd, "enrich_with_fulltext", spy_enrich)
        engine = DeepResearchEngine()
        engine.retrieve(
            "anticorrosion coating", source_types=["literature"], total_limit=5, per_source_cap=5
        )
        assert called["n"] == 1
    finally:
        get_settings.cache_clear()

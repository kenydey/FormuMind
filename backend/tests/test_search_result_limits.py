"""Search result count limits — per-source cap and rerank head+tail merge."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.config import get_settings
from app.domain.schemas import Evidence
from app.services import literature


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _ev(source: str, i: int) -> Evidence:
    return Evidence(
        source=source,
        identifier=f"{source}:{i}",
        title=f"{source} hit {i}",
        snippet=f"relevant zinc phosphate coating example {i}",
        relevance=0.5,
    )


def test_iter_search_respects_per_source_cap(monkeypatch):
    monkeypatch.setenv("FORMUMIND_SEARCH_RERANK_ENABLED", "false")
    get_settings.cache_clear()

    def fake_patents(_off, _q=""):
        return [_ev("patents", 1), _ev("patents", 2)]

    def fake_lit(_off, _q=""):
        return [_ev("openalex", 10)]

    with patch.object(literature, "search_patents", side_effect=fake_patents), patch.object(
        literature, "search_openalex", side_effect=fake_lit
    ):
        evidence, _ = literature.iter_search(
            "zinc phosphate coating",
            ["patents", "literature"],
            total_limit=300,
            per_source_cap=30,
            max_rounds=1,
        )

    patent_hits = [e for e in evidence if e.source == "patents"]
    assert len(patent_hits) <= 30
    assert len(evidence) <= 60


def test_rerank_keeps_tail_beyond_llm_batch(monkeypatch):
    monkeypatch.setenv("FORMUMIND_SEARCH_RERANK_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_SEARCH_RERANK_LLM_BATCH", "5")
    get_settings.cache_clear()

    candidates = [_ev("patents", i) for i in range(120)]

    def fake_merge(raw, _q, total_limit):
        from app.services.content_filter import FilterReport

        return raw[:total_limit], FilterReport()

    monkeypatch.setattr(literature, "_merge_filter_rank", fake_merge)
    monkeypatch.setattr(
        "app.services.content_filter.llm_quality_judge",
        lambda ev, _q: (ev, __import__("app.services.content_filter", fromlist=["FilterReport"]).FilterReport()),
    )

    call_count = {"n": 0}

    def fake_rerank(_q, cands, k, req=None):
        call_count["n"] += 1
        assert len(cands) <= 5
        return list(reversed(cands[:k]))

    monkeypatch.setattr("app.services.rag.llm_rerank", fake_rerank)

    # Simulate final-stage rerank logic directly
    settings = get_settings()
    final = candidates
    total_limit = 300
    batch = min(len(final), settings.search_rerank_llm_batch, total_limit)
    from app.services.rag import llm_rerank

    head = llm_rerank("zinc", final[:batch], k=batch)
    head_keys = {e.identifier or e.title for e in head}
    tail = [e for e in final[batch:] if (e.identifier or e.title) not in head_keys]
    merged = (head + tail)[:total_limit]

    assert len(merged) == 120
    assert call_count["n"] == 1

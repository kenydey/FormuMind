"""Tests for search_providers — offline mocks, no network."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.domain.schemas import Evidence
from app.services.search_providers import (
    merge_patent_evidence,
    search_cnipa_parallel,
    search_openalex,
    search_serpapi_chain,
    search_tavily,
)


def test_merge_patent_evidence_dedupes_by_normalized_id():
    a = Evidence(source="USPTO", identifier="US-9982145-B2", title="A", snippet="x", relevance=0.9)
    b = Evidence(source="EPO", identifier="US9982145B2", title="A dup", snippet="y", relevance=0.8)
    c = Evidence(source="EPO", identifier="EP3211048A1", title="B", snippet="z", relevance=0.7)
    merged = merge_patent_evidence([a, b], [c], limit=10)
    assert len(merged) == 2
    assert merged[0].identifier == "US-9982145-B2"
    assert merged[1].identifier == "EP3211048A1"


def test_search_openalex_parses_inverted_index(monkeypatch):
    payload = {
        "results": [
            {
                "id": "https://openalex.org/W1",
                "display_name": "Coating study",
                "doi": "https://doi.org/10.1234/test",
                "abstract_inverted_index": {"zinc": [0], "phosphate": [1]},
            }
        ]
    }

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            return FakeResp()

    monkeypatch.setattr("app.services.search_providers.httpx.Client", lambda **kw: FakeClient())
    hits = search_openalex("zinc coating", limit=5)
    assert len(hits) == 1
    assert hits[0].source == "OpenAlex"
    assert "zinc" in hits[0].snippet


def test_search_serpapi_chain_falls_back_to_patents(monkeypatch):
    calls: list[str] = []

    def fake_scholar(*a, **k):
        calls.append("scholar")
        return []

    def fake_patents(*a, **k):
        calls.append("patents")
        return [Evidence(source="Google Patents", identifier="US1", title="T", snippet="s", relevance=0.5)]

    monkeypatch.setattr("app.services.search_providers.search_serpapi_scholar", fake_scholar)
    monkeypatch.setattr("app.services.search_providers.search_serpapi_patents", fake_patents)
    hits = search_serpapi_chain("epoxy primer", limit=3)
    assert calls == ["scholar", "patents"]
    assert hits[0].identifier == "US1"


def test_search_tavily_maps_results(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {"url": "https://example.com", "title": "Web hit", "content": "body text"},
                ]
            }

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None):
            return FakeResp()

    monkeypatch.setattr("app.services.search_providers.httpx.Client", lambda **kw: FakeClient())

    from app.config import get_settings

    s = get_settings()
    object.__setattr__(s, "tavily_api_key", "tvly-test")
    hits = search_tavily("防腐涂料", limit=2, settings=s)
    assert hits[0].source == "Tavily"
    assert hits[0].title == "Web hit"


def test_search_cnipa_parallel_uses_tavily_first(monkeypatch):
    monkeypatch.setattr(
        "app.services.search_providers.search_tavily",
        lambda *a, **k: [
            Evidence(source="Tavily", identifier="u", title="CN patent", snippet="s", relevance=0.6)
        ],
    )
    from app.config import get_settings

    s = get_settings()
    object.__setattr__(s, "tavily_api_key", "tvly-test")
    hits = search_cnipa_parallel("水性防腐", limit=2, settings=s)
    assert hits[0].source == "CNIPA (web)"


def test_literature_search_internet_prefers_tavily(monkeypatch):
    from app.config import get_settings
    from app.services import literature

    get_settings.cache_clear()
    s = get_settings()
    object.__setattr__(s, "tavily_api_key", "tvly-test")

    def fake_tavily(q, limit, offset, *, settings=None, topic="general"):
        return [Evidence(source="Tavily", identifier="u", title="T", snippet="s", relevance=0.5)]

    def fake_web(q, limit, offset):
        pytest.fail("DDG should not run when Tavily returns hits")

    monkeypatch.setattr("app.services.search_providers.search_tavily", fake_tavily)
    monkeypatch.setattr(literature, "search_web", fake_web)
    hits = literature.search_internet("epoxy", limit=2)
    assert hits[0].source == "Tavily"
    get_settings.cache_clear()


def test_arxiv_disabled_returns_empty():
    from app.config import get_settings
    from app.services import literature

    get_settings.cache_clear()
    s = get_settings()
    object.__setattr__(s, "arxiv_search_enabled", False)
    assert literature.search_arxiv("coating", limit=2) == []
    get_settings.cache_clear()

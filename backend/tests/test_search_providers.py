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
    from app.services.runtime_secrets import get_runtime_secrets
    from app.services.secrets_store import reload_settings

    get_settings.cache_clear()
    reload_settings()
    get_runtime_secrets().set("tavily_api_key", "tvly-test")
    s = get_settings()
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
    from app.services.runtime_secrets import get_runtime_secrets
    from app.services.secrets_store import reload_settings

    get_settings.cache_clear()
    reload_settings()
    get_runtime_secrets().set("tavily_api_key", "tvly-test")
    s = get_settings()
    hits = search_cnipa_parallel("水性防腐", limit=2, settings=s)
    assert hits[0].source == "CNIPA (web)"


def test_literature_search_internet_prefers_tavily(monkeypatch):
    from app.config import get_settings
    from app.services import literature
    from app.services.runtime_secrets import get_runtime_secrets
    from app.services.secrets_store import reload_settings

    get_settings.cache_clear()
    reload_settings()
    get_runtime_secrets().set("tavily_api_key", "tvly-test")
    s = get_settings()

    def fake_tavily(q, limit, offset, *, settings=None, topic="general"):
        return [Evidence(source="Tavily", identifier="u", title="T", snippet="s", relevance=0.5)]

    def fake_web(q, limit, offset):
        pytest.fail("DDG should not run when Tavily returns hits")

    monkeypatch.setattr("app.services.search_providers.search_tavily", fake_tavily)
    monkeypatch.setattr(literature, "search_web", fake_web)
    hits = literature.search_internet("epoxy", limit=2)
    assert hits[0].source == "Tavily"
    get_settings.cache_clear()
    reload_settings()


def test_arxiv_disabled_returns_empty():
    from app.config import get_settings
    from app.services import literature

    get_settings.cache_clear()
    s = get_settings()
    object.__setattr__(s, "arxiv_search_enabled", False)
    assert literature.search_arxiv("coating", limit=2) == []
    get_settings.cache_clear()


# ── internet search chain: Tavily → SerpAPI → DuckDuckGo ─────────────────────
#
# The chain exists so a deployment holding a key never silently falls to the
# keyless tier. Each provider names itself in `Evidence.source` — "this tier's
# results are poor" is only a checkable claim when you can tell which tier
# answered, and DuckDuckGo used to report the generic source="Internet".


def _hit(source: str, ident: str = "https://e.example/1") -> Evidence:
    return Evidence(source=source, identifier=ident, title="t", snippet="s", relevance=0.9)


@pytest.fixture()
def _keys(monkeypatch):
    """Set exactly the provider keys a test wants, and nothing else.

    `effective_setting` consults the runtime-secrets overlay *before* the
    environment, and earlier tests in this file set `tavily_api_key` there
    without clearing it. Clearing env vars alone therefore leaves a Tavily key
    live and makes these tests order-dependent — so the overlay is reset too.
    """
    from app.config import get_settings
    from app.services.runtime_secrets import get_runtime_secrets

    def apply(tavily: str | None = None, serpapi: str | None = None, allow_ddgs: bool = True):
        secrets = get_runtime_secrets()
        for attr, name, val in (("tavily_api_key", "FORMUMIND_TAVILY_API_KEY", tavily),
                                ("serpapi_api_key", "FORMUMIND_SERPAPI_API_KEY", serpapi)):
            secrets.set(attr, val)
            if val:
                monkeypatch.setenv(name, val)
            else:
                monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("FORMUMIND_WEB_SEARCH_ALLOW_DDGS", "true" if allow_ddgs else "false")
        get_settings.cache_clear()

    yield apply
    get_runtime_secrets().clear()
    get_settings.cache_clear()


def test_serpapi_serves_general_web_when_tavily_is_absent(_keys, monkeypatch):
    """The gap this closes: SerpAPI was wired for scholar and patents only, so a
    SerpAPI-only deployment still dropped to DuckDuckGo for general queries."""
    from app.services import literature

    _keys(serpapi="sk-serp")
    monkeypatch.setattr(
        "app.services.search_providers.search_serpapi_web",
        lambda q, l=5, o=0, **kw: [_hit("SerpAPI (web)")],
    )
    ddgs_called: list[str] = []
    monkeypatch.setattr(literature, "search_web",
                        lambda q, l=5, o=0: ddgs_called.append(q) or [])

    hits = literature.search_internet("epoxy primer")
    assert [h.source for h in hits] == ["SerpAPI (web)"]
    assert ddgs_called == [], "DuckDuckGo must not run once a keyed tier answered"


def test_tavily_wins_over_serpapi(_keys, monkeypatch):
    from app.services import literature

    _keys(tavily="tv", serpapi="sk-serp")
    monkeypatch.setattr("app.services.search_providers.search_tavily",
                        lambda q, l=5, o=0, **kw: [_hit("Tavily")])
    serp_called: list[str] = []
    monkeypatch.setattr("app.services.search_providers.search_serpapi_web",
                        lambda q, l=5, o=0, **kw: serp_called.append(q) or [])

    assert [h.source for h in literature.search_internet("q")] == ["Tavily"]
    assert serp_called == []


def test_empty_keyed_result_falls_through_to_the_next_tier(_keys, monkeypatch):
    """A configured provider that returns nothing must not end the search."""
    from app.services import literature

    _keys(tavily="tv", serpapi="sk-serp")
    monkeypatch.setattr("app.services.search_providers.search_tavily",
                        lambda q, l=5, o=0, **kw: [])
    monkeypatch.setattr("app.services.search_providers.search_serpapi_web",
                        lambda q, l=5, o=0, **kw: [_hit("SerpAPI (web)")])

    assert [h.source for h in literature.search_internet("q")] == ["SerpAPI (web)"]


def test_ddgs_can_be_switched_off_and_returns_nothing_rather_than_junk(_keys, monkeypatch):
    """Disabling the keyless tier must return empty, not quietly use it anyway.

    Returning low-quality hits while reporting success is the failure this
    switch exists to avoid.
    """
    from app.services import literature

    _keys(allow_ddgs=False)
    called: list[str] = []
    monkeypatch.setattr(literature, "search_web", lambda q, l=5, o=0: called.append(q) or [_hit("DuckDuckGo")])

    assert literature.search_internet("q") == []
    assert called == [], "the disabled tier must not be consulted at all"


def test_ddgs_remains_the_default_no_key_path(_keys, monkeypatch):
    """Working with no API keys at all is a design property, not an oversight."""
    from app.services import literature

    _keys()  # no keys, fallback allowed
    monkeypatch.setattr(literature, "search_web", lambda q, l=5, o=0: [_hit("DuckDuckGo")])
    assert [h.source for h in literature.search_internet("q")] == ["DuckDuckGo"]


def test_every_web_tier_is_still_treated_as_web_by_the_filters():
    """Renaming the source must not disable near-dup filtering for web results.

    Both `_is_weblike` checks match on substrings, so this pins that the new
    provider names still land inside the web bucket.
    """
    from app.services.content_filter import _is_weblike as cf_weblike
    from app.services.literature import _is_weblike as lit_weblike

    for name in ("Tavily", "SerpAPI (web)", "DuckDuckGo"):
        ev = _hit(name)
        assert cf_weblike(ev), f"{name} escaped the content filter's web bucket"
        assert lit_weblike(ev), f"{name} escaped literature's web bucket"

    # ...while SerpAPI's scholar/patent results must stay authoritative, which
    # is exactly why the general-web tier carries the "(web)" suffix.
    from app.services.literature import _is_patent_or_literature

    assert _is_patent_or_literature(_hit("SerpAPI"))
    assert not _is_patent_or_literature(_hit("SerpAPI (web)"))


def test_serpapi_web_results_are_classified_as_web_not_literature(monkeypatch):
    """Asserted on what the provider really emits, not on a hand-written label.

    SerpAPI also backs scholar and patent search, so a bare "SerpAPI" source is
    already classified as authoritative literature. General-web hits must carry
    the "(web)" marker or they inherit that standing and skip the web-specific
    near-dup filtering.
    """
    from app.config import get_settings
    from app.services import search_providers as sp
    from app.services.literature import _is_patent_or_literature, _is_weblike

    monkeypatch.setenv("FORMUMIND_SERPAPI_API_KEY", "sk-serp")
    get_settings.cache_clear()
    monkeypatch.setattr(
        sp, "_serpapi_search",
        lambda engine, q, key, limit, offset, extra_params=None: {
            "organic_results": [
                {"link": "https://blog.example/x", "title": "SEO blog", "snippet": "waterborne epoxy"}
            ]
        },
    )

    hits = sp.search_serpapi_web("waterborne epoxy", limit=3)
    assert hits, "the provider returned nothing"
    ev = hits[0]
    assert _is_weblike(ev), f"general web hit not treated as web: source={ev.source!r}"
    assert not _is_patent_or_literature(ev), (
        f"general web hit counted as literature: source={ev.source!r}"
    )
    get_settings.cache_clear()

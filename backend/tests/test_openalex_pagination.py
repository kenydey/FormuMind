"""OpenAlex pagination across page boundaries."""
from __future__ import annotations

from unittest.mock import patch

from app.services.search_providers import search_openalex


def _page_payload(page: int, n: int = 25):
    return {
        "results": [
            {
                "id": f"https://openalex.org/W{page}-{i}",
                "display_name": f"Work {page}-{i}",
                "doi": f"https://doi.org/10.1234/p{page}{i}",
                "abstract_inverted_index": {"test": [0]},
            }
            for i in range(n)
        ]
    }


def test_openalex_offset_spans_page_boundary(monkeypatch):
    pages: dict[int, dict] = {1: _page_payload(1), 2: _page_payload(2)}

    class FakeResp:
        def __init__(self, page: int):
            self._page = page

        def raise_for_status(self):
            return None

        def json(self):
            return pages[self._page]

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            page = int((params or {}).get("page", 1))
            return FakeResp(page)

    monkeypatch.setattr("app.services.search_providers.httpx.Client", lambda **kw: FakeClient())
    hits = search_openalex("coating", limit=10, offset=20)
    assert len(hits) == 10
    assert hits[0].identifier == "10.1234/p120"
    assert hits[-1].identifier == "10.1234/p24"


def test_openalex_returns_empty_when_no_results(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": []}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            return FakeResp()

    monkeypatch.setattr("app.services.search_providers.httpx.Client", lambda **kw: FakeClient())
    assert search_openalex("empty", limit=5, offset=30) == []

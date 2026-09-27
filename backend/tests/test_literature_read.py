"""W2-2 (P1-7): search→read two-stage — read_passages + has_fulltext marking."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.domain.schemas import Evidence
from app.services import literature


def _ev(identifier="10.1016/j.porgcoat.2019.105338", **kw):
    d = dict(
        source="openalex",
        identifier=identifier,
        title="epoxy coating corrosion protection",
        snippet="snippet about epoxy coating corrosion protection with zinc phosphate pigment",
        relevance=0.9,
    )
    d.update(kw)
    return Evidence(**d)


_PAGE_TEXT = (
    "<!-- page:1 -->\n# 配方设计\n" + "环氧树脂 E-51 与固化剂配比说明。" * 60 + "\n"
    "<!-- page:2 -->\n# 性能测试\n" + "拉伸强度与附着力测试数据记录。" * 60 + "\n"
    "<!-- page:3 -->\n# 结论\n" + "综合性能满足技术要求。" * 60 + "\n"
)


class _Doc:
    id = "doc-1"
    full_text = _PAGE_TEXT


class _Store:
    def __init__(self, doc=None):
        self._doc = doc

    def find_by_origin_url(self, origin_url):
        return self._doc


@pytest.fixture()
def _store_with_doc(monkeypatch):
    monkeypatch.setattr(
        "app.db.source_store.get_source_store", lambda: _Store(_Doc())
    )


@pytest.fixture()
def _store_empty(monkeypatch):
    monkeypatch.setattr(
        "app.db.source_store.get_source_store", lambda: _Store(None)
    )


@pytest.fixture()
def _enrich_off(monkeypatch):
    monkeypatch.setenv("FORMUMIND_FULLTEXT_ENRICH", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_no_identifier(_store_with_doc):
    passages, meta = literature.read_passages(_ev(identifier=""))
    assert passages == [] and meta["reason"] == "no_identifier"


def test_persisted_fulltext_returns_page_passages(_store_with_doc):
    passages, meta = literature.read_passages(_ev())
    assert meta["ok"] is True
    assert meta["source_id"] == "doc-1"
    pages = [p.page_no for p in passages]
    assert 1 in pages and 2 in pages
    assert all(p.text.strip() for p in passages)
    assert all(p.char_start is not None for p in passages)


def test_no_fulltext_enrich_off(_store_empty, _enrich_off):
    passages, meta = literature.read_passages(_ev())
    assert passages == [] and meta["reason"] == "no_fulltext"


def test_ondemand_fetch_success(monkeypatch):
    monkeypatch.setenv("FORMUMIND_FULLTEXT_ENRICH", "true")
    get_settings.cache_clear()
    shared = _Store(None)
    monkeypatch.setattr(
        "app.db.source_store.get_source_store", lambda: shared
    )
    holder = {}

    def fake_enrich(evidence, **kw):
        holder["called"] = True
        shared._doc = _Doc()  # simulate the fetcher persisting the document

    monkeypatch.setattr(
        "app.services.fulltext_fetcher.enrich_search_results", fake_enrich
    )
    try:
        passages, meta = literature.read_passages(_ev())
    finally:
        get_settings.cache_clear()
    assert holder.get("called")
    assert meta["ok"] is True and passages


def test_ondemand_fetch_still_missing(_store_empty, monkeypatch):
    monkeypatch.setenv("FORMUMIND_FULLTEXT_ENRICH", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.services.fulltext_fetcher.enrich_search_results",
        lambda *a, **k: ([], {}),
    )
    try:
        passages, meta = literature.read_passages(_ev())
    finally:
        get_settings.cache_clear()
    assert passages == [] and meta["reason"] == "fetch_failed"


def test_max_pages_truncation(_store_with_doc):
    passages, meta = literature.read_passages(_ev(), max_pages=1)
    assert meta["truncated"] is True
    assert {p.page_no for p in passages} == {1}


def test_max_chars_truncation(_store_with_doc):
    passages, meta = literature.read_passages(_ev(), max_chars=100)
    assert meta["truncated"] is True
    assert len(passages) >= 1


def test_fail_open_on_store_error(monkeypatch):
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("app.db.source_store.get_source_store", _boom)
    passages, meta = literature.read_passages(_ev())
    assert passages == [] and meta["reason"] == "error"


def test_iter_search_marks_has_fulltext(monkeypatch):
    evs = [_ev("10.1/doi1"), _ev("10.1/doi2")]

    def fake_fetch(off):
        return list(evs) if off == 0 else []

    def fake_build_streams(*a, **k):
        return [{"name": "mock", "fetch": fake_fetch, "cursor": 0,
                 "paged": True, "done": False}]

    class _SelectiveStore:
        def find_by_origin_url(self, origin):
            return _Doc() if "doi1" in (origin or "") else None

    monkeypatch.setattr(literature, "_build_streams", fake_build_streams)
    monkeypatch.setattr(
        "app.db.source_store.get_source_store", lambda: _SelectiveStore()
    )
    final, _ = literature.iter_search(
        "epoxy", ["literature"], req=None, total_limit=10,
        per_source_cap=10, max_rounds=2,
    )
    by_id = {e.identifier: e for e in final}
    assert by_id["10.1/doi1"].has_fulltext is True
    assert by_id["10.1/doi2"].has_fulltext is False

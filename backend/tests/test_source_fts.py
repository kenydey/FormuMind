"""W2-1 (P1-6): chunk-level FTS5 index over full-text chunks."""
from __future__ import annotations

import pytest

from app.db.database import Base, make_engine, make_session_factory
from app.services import source_fts


@pytest.fixture()
def sf(tmp_path):
    db_path = tmp_path / "source_fts_test.db"
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _rows():
    return [
        {
            "text": "环氧树脂 E-51 与聚酰胺固化剂按 100:40 配比混合，室温固化 7 天。",
            "heading_path": "配方设计",
            "page_no": 3,
            "paragraph_idx": 0,
            "offset_start": 0,
            "offset_end": 120,
        },
        {
            "text": "拉伸强度测试结果为 45 MPa，断裂伸长率 8%。",
            "heading_path": "性能测试",
            "page_no": 5,
            "paragraph_idx": 2,
            "offset_start": 400,
            "offset_end": 460,
        },
        {
            "text": "The epoxy cure schedule requires 80C for 2 hours post-cure.",
            "heading_path": "Curing",
            "page_no": 7,
            "paragraph_idx": 0,
            "offset_start": 900,
            "offset_end": 960,
        },
    ]


def test_index_and_search_cjk(sf):
    n = source_fts.index_source_chunks("s1", _rows(), session_factory=sf)
    assert n == 3
    hits = source_fts.search_chunks("环氧 固化剂", session_factory=sf)
    assert hits, "CJK query should match"
    assert hits[0]["source_id"] == "s1"
    assert hits[0]["page_no"] == 3


def test_page_numbers_preserved(sf):
    source_fts.index_source_chunks("s1", _rows(), session_factory=sf)
    hits = source_fts.search_chunks("拉伸强度", session_factory=sf)
    assert hits and hits[0]["page_no"] == 5


def test_section_title_weight(sf):
    # "性能测试" appears only as section title of chunk 2; "测试结果" text is
    # in chunk 2 body. Querying the section title must rank chunk 2 first.
    source_fts.index_source_chunks("s1", _rows(), session_factory=sf)
    hits = source_fts.search_chunks("性能测试", session_factory=sf)
    assert hits and hits[0]["page_no"] == 5


def test_raw_text_not_cjk_expanded(sf):
    source_fts.index_source_chunks("s1", _rows(), session_factory=sf)
    hits = source_fts.search_chunks("环氧", session_factory=sf)
    assert hits
    for h in hits:
        assert "环 氧" not in h["text"], "display text must not be CJK-expanded"
        assert "配 方" not in (h["section_title"] or "")
    assert hits[0]["text"].startswith("环氧树脂 E-51")


def test_char_offsets_preserved(sf):
    source_fts.index_source_chunks("s1", _rows(), session_factory=sf)
    hits = source_fts.search_chunks("环氧 固化剂", session_factory=sf)
    assert hits
    first = hits[0]
    assert first["page_no"] == 3
    assert first["char_start"] == 0 and first["char_end"] == 120


def test_char_offsets_explicit_keys(sf):
    rows = [
        {
            "text": "环氧树脂测试文本。",
            "heading_path": "",
            "page_no": 1,
            "char_start": 10,
            "char_end": 20,
            "offset_start": 999,  # explicit char_* keys win over offset_*
            "offset_end": 999,
        }
    ]
    source_fts.index_source_chunks("s1", rows, session_factory=sf)
    hits = source_fts.search_chunks("环氧树脂", session_factory=sf)
    assert hits and hits[0]["char_start"] == 10 and hits[0]["char_end"] == 20


def test_source_id_filter(sf):
    source_fts.index_source_chunks("s1", _rows(), session_factory=sf)
    source_fts.index_source_chunks(
        "s2",
        [{"text": "环氧树脂的另一种配方体系说明。", "heading_path": "", "page_no": 1}],
        session_factory=sf,
    )
    hits = source_fts.search_chunks("环氧树脂", source_ids=["s2"], session_factory=sf)
    assert hits and all(h["source_id"] == "s2" for h in hits)


def test_reindex_is_idempotent(sf):
    source_fts.index_source_chunks("s1", _rows(), session_factory=sf)
    n2 = source_fts.index_source_chunks("s1", _rows(), session_factory=sf)
    assert n2 == 3
    hits = source_fts.search_chunks("环氧", session_factory=sf)
    assert sum(1 for h in hits if h["source_id"] == "s1") == len(
        [h for h in hits if h["source_id"] == "s1"]
    )
    # Re-index with fewer rows must drop the stale ones.
    source_fts.index_source_chunks("s1", _rows()[:1], session_factory=sf)
    hits = source_fts.search_chunks("拉伸强度", session_factory=sf)
    assert not [h for h in hits if h["source_id"] == "s1"]


def test_delete_source_chunks(sf):
    source_fts.index_source_chunks("s1", _rows(), session_factory=sf)
    assert source_fts.delete_source_chunks("s1", session_factory=sf) >= 1
    assert source_fts.search_chunks("环氧", source_ids=["s1"], session_factory=sf) == []


def test_empty_query_returns_empty(sf):
    assert source_fts.search_chunks("", session_factory=sf) == []
    assert source_fts.search_chunks("   ", session_factory=sf) == []


def test_fail_open_on_broken_factory(monkeypatch):
    def _boom():
        raise RuntimeError("no db")

    monkeypatch.setattr(source_fts, "_session_factory", _boom)
    assert source_fts.index_source_chunks("s1", _rows()) == 0
    assert source_fts.search_chunks("环氧") == []
    assert source_fts.delete_source_chunks("s1") == 0


def test_latin_query(sf):
    source_fts.index_source_chunks("s1", _rows(), session_factory=sf)
    hits = source_fts.search_chunks("epoxy cure", session_factory=sf)
    assert hits and hits[0]["page_no"] == 7


def test_reclaim_expired(sf, monkeypatch):
    class _S:
        id = "s9"

    class _Store:
        def list_archived_expired(self, *, days, limit=500):
            assert days == 30
            return [_S()]

    import app.services.source_fts as mod  # noqa: F401  (import side-effect check)

    # reclaim_expired imports get_source_store lazily from app.db.source_store;
    # patch that module so the lazy import picks up the fake.
    import app.db.source_store as ss_mod

    monkeypatch.setattr(ss_mod, "get_source_store", lambda: _Store())
    source_fts.index_source_chunks("s9", _rows(), session_factory=sf)
    assert source_fts.search_chunks("环氧", source_ids=["s9"], session_factory=sf)
    n = source_fts.reclaim_expired(30, session_factory=sf)
    assert n >= 1
    assert source_fts.search_chunks("环氧", source_ids=["s9"], session_factory=sf) == []

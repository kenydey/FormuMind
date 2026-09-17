"""Ingest-time quality gate: dirty sources never become document_chunks."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.services import kb_index
from app.services.kb_retrieval_gate import gate_ingest_rows, ingest_block_reason_for_source
from app.services.ingestion import ingest_url


GOOD_TEXT = (
    "# 防腐底漆\n\n"
    "环氧树脂是一种优异的防腐涂料成膜物质，添加磷酸锌可提升盐雾耐受时间。"
    "实施例盐雾试验七百二十小时无起泡，附着力划格法零级。"
    "固化剂采用异佛尔酮二胺二十四质量份，研磨后喷涂固化。"
)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_CONTENT_FILTER_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_KB_V2_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_store_mod
    import app.db.database as db_mod
    import app.db.source_store as source_store_mod
    from app.db.chunk_store import ChunkStore
    from app.db.source_store import SourceStore

    engine = make_engine(f"sqlite:///{tmp_path}/kb_ingest_gate.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)
    return src, chk


def test_index_source_writes_zero_for_blocked_origin(stores):
    src, chk = stores
    dirty = src.create(
        filename="https://www.alibaba.com/product/epoxy",
        title="Buy epoxy cheap",
        source_kind="web",
        full_text=GOOD_TEXT,
        content_hash="dirty-block",
        origin_url="https://www.alibaba.com/product/epoxy-cheap",
    )
    n = kb_index.index_source(dirty, GOOD_TEXT, embed=False)
    assert n == 0
    assert chk.get_by_source(dirty) == []
    assert ingest_block_reason_for_source(dirty) == "blocked_domain"


def test_index_source_clears_prior_chunks_when_blocked(stores):
    """Re-index of a later-blocked origin must wipe leftover rows."""
    src, chk = stores
    sid = src.create(
        filename="dirty.md",
        title="dirty",
        source_kind="web",
        full_text=GOOD_TEXT,
        content_hash="prior",
        origin_url="https://alibaba.com/item/9",
    )
    chk.replace_for_source(sid, [{"text": GOOD_TEXT}])
    assert len(chk.get_by_source(sid)) == 1
    assert kb_index.index_source(sid, GOOD_TEXT, embed=False) == 0
    assert chk.get_by_source(sid) == []


def test_index_source_writes_zero_for_all_garbage(stores):
    src, chk = stores
    sid = src.create(
        filename="junk.md",
        title="junk",
        source_kind="local",
        full_text="!!!@@@###$$$",
        content_hash="junk",
        origin_url=None,
    )
    # Bypass the >30 char prefilter by feeding long symbol soup.
    garbage = ("!!!@@@###$$$%%%^^^&&&***" * 8) + "\n\n" + ("$$$%%%^^^&&&***!!!" * 8)
    n = kb_index.index_source(sid, garbage, embed=False)
    assert n == 0
    assert chk.get_by_source(sid) == []


def test_index_source_keeps_clean_local(stores):
    src, chk = stores
    sid = src.create(
        filename="good.md",
        title="防腐涂料研究",
        source_kind="local",
        full_text=GOOD_TEXT,
        content_hash="good",
        origin_url=None,
    )
    n = kb_index.index_source(sid, GOOD_TEXT, embed=False)
    assert n >= 1
    assert len(chk.get_by_source(sid)) == n


def test_index_source_blocked_when_filter_disabled(stores, monkeypatch):
    monkeypatch.setenv("FORMUMIND_CONTENT_FILTER_ENABLED", "false")
    get_settings.cache_clear()
    src, chk = stores
    dirty = src.create(
        filename="alibaba.md",
        title="market",
        source_kind="web",
        full_text=GOOD_TEXT,
        content_hash="off",
        origin_url="https://www.alibaba.com/product/x",
    )
    n = kb_index.index_source(dirty, GOOD_TEXT, embed=False)
    assert n >= 1
    assert len(chk.get_by_source(dirty)) == n


def test_gate_ingest_rows_unit():
    kept, reason = gate_ingest_rows(
        [{"text": "!!!@@@###"}, {"text": GOOD_TEXT}],
        source_id=None,
    )
    assert reason is None
    assert len(kept) == 1
    assert kept[0]["text"] == GOOD_TEXT


def test_ingest_url_skips_blocked_without_network(monkeypatch):
    """Blocked hosts must not hit the network."""
    called = []

    class _Boom:
        def __init__(self, *a, **k):
            called.append("client")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            called.append("get")
            raise AssertionError("must not fetch blocked URL")

    monkeypatch.setattr("httpx.Client", _Boom)
    out = ingest_url("https://www.alibaba.com/product/epoxy-cheap", persist=False)
    assert out.extraction_status == "skipped"
    assert out.source_id is None
    assert called == []
    assert "黑名单" in (out.evidence[0].snippet or "")

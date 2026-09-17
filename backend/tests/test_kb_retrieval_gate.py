"""Corpus quality gate on hybrid / probe / recommend path."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.services.kb_retrieval_gate import (
    drop_reason_for_chunk,
    gate_chunk_indices,
    is_blocked_origin_url,
    is_garbage_chunk_text,
)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_CONTENT_FILTER_ENABLED", "true")
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

    engine = make_engine(f"sqlite:///{tmp_path}/kb_gate.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)
    return src, chk


def test_is_blocked_origin_url_matches_alibaba():
    assert is_blocked_origin_url("https://www.alibaba.com/product/xyz")
    assert not is_blocked_origin_url("https://pubs.acs.org/doi/10.1")
    assert not is_blocked_origin_url(None)


def test_garbage_chunk_text():
    assert is_garbage_chunk_text("!!!@@@###", min_chars=5)
    assert not is_garbage_chunk_text(
        "环氧树脂防腐底漆磷酸锌防锈颜料盐雾试验报告摘要文本足够长。",
        min_chars=20,
    )


def test_hybrid_drops_blocked_origin_from_topk(stores, monkeypatch):
    """Dirty marketplace origin_url must not survive hybrid top-k."""
    src, chk = stores
    good = src.create(
        filename="good.md",
        title="防腐涂料研究",
        source_kind="local",
        full_text="环氧树脂防腐底漆磷酸锌",
        content_hash="good",
        origin_url=None,
    )
    dirty = src.create(
        filename="dirty.md",
        title="Buy epoxy paint cheap",
        source_kind="web",
        full_text="环氧 防腐 底漆 bulk wholesale epoxy coating bargain",
        content_hash="dirty",
        origin_url="https://www.alibaba.com/product/epoxy-cheap",
    )
    # Same embedding so BM25/text decides; both match the query tokens.
    emb = [1.0, 0.0]
    chk.replace_for_source(
        good,
        [
            {
                "text": "环氧树脂是一种优异的防腐涂料成膜物质，添加磷酸锌可提升盐雾耐受时间。",
                "embedding": emb,
                "embedding_model": "m",
            }
        ],
    )
    chk.replace_for_source(
        dirty,
        [
            {
                "text": "环氧 防腐 底漆 Buy epoxy paint cheap bulk wholesale on alibaba marketplace listing.",
                "embedding": emb,
                "embedding_model": "m",
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.kb_index._embed_texts",
        lambda texts, model_name=None: [[0.9, 0.1]],
    )
    monkeypatch.setattr(
        "app.services.kb_retrieval_gate._wiki_source_ids",
        lambda: set(),
    )

    from app.services.hybrid_search import hybrid_search_scored

    hits = hybrid_search_scored("环氧 防腐 底漆", top_k=5, alpha=0.5)
    source_ids = {getattr(h.chunk, "source_id", None) for h in hits}
    assert good in source_ids
    assert dirty not in source_ids


def test_hybrid_blocked_origin_returns_when_filter_disabled(stores, monkeypatch):
    src, chk = stores
    monkeypatch.setenv("FORMUMIND_CONTENT_FILTER_ENABLED", "false")
    get_settings.cache_clear()

    dirty = src.create(
        filename="dirty.md",
        title="Buy epoxy",
        source_kind="web",
        full_text="环氧 防腐 底漆 bargain",
        content_hash="d2",
        origin_url="https://alibaba.com/item/1",
    )
    chk.replace_for_source(
        dirty,
        [
            {
                "text": "环氧 防腐 底漆 Buy epoxy paint cheap bulk wholesale listing text.",
                "embedding": [1.0, 0.0],
                "embedding_model": "m",
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.kb_index._embed_texts",
        lambda texts, model_name=None: [[1.0, 0.0]],
    )
    monkeypatch.setattr("app.services.kb_retrieval_gate._wiki_source_ids", lambda: set())

    from app.services.hybrid_search import hybrid_search_scored

    hits = hybrid_search_scored("环氧 防腐", top_k=3, alpha=0.5)
    assert any(getattr(h.chunk, "source_id", None) == dirty for h in hits)


def test_gate_chunk_indices_skips_blocked(monkeypatch):
    class _C:
        def __init__(self, sid, text):
            self.source_id = sid
            self.text = text

    chunks = [
        _C("a", "环氧树脂防腐底漆磷酸锌防锈颜料盐雾试验报告摘要文本足够长。"),
        _C("b", "环氧树脂防腐底漆磷酸锌防锈颜料盐雾试验报告摘要文本足够长。"),
    ]
    meta = {
        "a": type("M", (), {"origin_url": None, "source_kind": "local"})(),
        "b": type("M", (), {"origin_url": "https://www.alibaba.com/x", "source_kind": "web"})(),
    }
    monkeypatch.setattr(
        "app.services.kb_retrieval_gate._load_source_meta",
        lambda ids: meta,
    )
    monkeypatch.setattr("app.services.kb_retrieval_gate._wiki_source_ids", lambda: set())
    kept = gate_chunk_indices(chunks, [1, 0], top_k=2)
    assert kept == [0]
    assert drop_reason_for_chunk(chunks[1], source_meta=meta, wiki_ids=set()) == "blocked_domain"

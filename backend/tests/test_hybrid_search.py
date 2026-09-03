"""Task 2.5: hybrid BM25 + vector search tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    """Isolated SourceStore + ChunkStore sharing one temp SQLite DB."""
    import app.db.chunk_store as chunk_store_mod
    import app.db.database as db_mod
    import app.db.source_store as source_store_mod
    from app.db.source_store import SourceStore

    engine = make_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)
    return src, chk


def _client() -> TestClient:
    from app.main import app

    return TestClient(app)


# ── test 1: empty corpus ─────────────────────────────────────────────────────


def test_hybrid_empty(stores):
    """Empty corpus → empty list."""
    from app.services.hybrid_search import hybrid_search

    results = hybrid_search("anything")
    assert results == []


# ── test 2: single chunk ─────────────────────────────────────────────────────


def test_hybrid_single_chunk(stores):
    """Ingest 1 doc → search returns results."""
    client = _client()
    resp = client.post(
        "/api/kb/ingest",
        json={"text": "环氧树脂是一种优异的防腐涂料成膜物质，具有高附着力和耐化学性。", "title": "Test Doc"},
    )
    assert resp.status_code == 200, f"ingest failed: {resp.status_code} {resp.json()}"

    resp2 = client.post(
        "/api/kb/hybrid-search",
        json={"query": "环氧树脂防腐", "top_k": 5},
    )
    assert resp2.status_code == 200, f"hybrid-search failed: {resp2.status_code} {resp2.json()}"
    data = resp2.json()
    assert len(data) >= 1, f"expected at least 1 result, got {data}"
    assert "环氧" in data[0]["text"]


# ── test 3: ranking by relevance ────────────────────────────────────────────


def test_hybrid_ranks_by_relevance(stores, monkeypatch):
    """Two docs (one highly matching, one not) → matching doc ranks first."""
    src, chk = stores

    # Doc 1: highly relevant — epoxy anticorrosion
    sid1 = src.create(
        filename="d1.md",
        title="防腐涂料研究",
        source_kind="local",
        full_text="环氧树脂防腐底漆磷酸锌防锈颜料",
        content_hash="h1",
    )
    # Doc 2: irrelevant — cake recipe
    sid2 = src.create(
        filename="d2.md",
        title="烘焙食谱",
        source_kind="local",
        full_text="巧克力蛋糕配方 面粉 糖 鸡蛋 黄油",
        content_hash="h2",
    )

    chk.replace_for_source(
        sid1,
        [
            {
                "text": "环氧树脂是一种优异的防腐涂料成膜物质，添加磷酸锌可提升盐雾耐受时间。",
                "embedding": [1.0, 0.0],
                "embedding_model": "m",
            }
        ],
    )
    chk.replace_for_source(
        sid2,
        [
            {
                "text": "巧克力蛋糕配方需要面粉糖和鸡蛋黄油混合烘烤。",
                "embedding": [0.0, 1.0],
                "embedding_model": "m",
            }
        ],
    )

    # Mock embeddings so the epoxy chunk aligns with a corrosion query
    monkeypatch.setattr(
        "app.services.kb_index._embed_texts",
        lambda texts: [[0.9, 0.1]],
    )

    from app.services.hybrid_search import hybrid_search

    results = hybrid_search("防腐涂料 环氧 磷酸锌", top_k=5)
    assert len(results) >= 1, f"expected at least 1 result, got {len(results)}"
    # The top result must contain epoxy/corrosion content, not cake
    top_text = results[0].text
    assert "环氧" in top_text or "防腐" in top_text, (
        f"expected top result to be about epoxy/corrosion, got: {top_text[:80]}"
    )

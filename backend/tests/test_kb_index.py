"""KB P2 tests — persistent chunk store, kb_index service, chat/API integration."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.domain.schemas import Evidence
from app.services import kb_index


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
    import app.db.source_store as source_store_mod
    from app.db.source_store import SourceStore

    engine = make_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    return src, chk


MD = """# 防腐底漆专利

## 实施例 1

环氧树脂 E51 一百质量份，异佛尔酮二胺固化剂二十四份，磷酸锌防锈颜料十五份，混合研磨后喷涂固化。盐雾试验通过七百二十小时无起泡无锈蚀，附着力划格法零级。

## 实施例 2

对比样使用聚酰胺固化剂六十五份，其余组分不变。盐雾试验四百八十小时出现轻微锈蚀。
"""


# ── chunk store ──────────────────────────────────────────────────────────────


def test_chunk_store_replace_is_idempotent(stores):
    _, chk = stores
    n1 = chk.replace_for_source("s1", [{"text": "aaa"}, {"text": "bbb", "heading_path": "H"}])
    n2 = chk.replace_for_source("s1", [{"text": "ccc"}])
    assert (n1, n2) == (2, 1)
    rows = chk.get_by_source("s1")
    assert [r.text for r in rows] == ["ccc"]
    assert chk.counts() == (1, 0)


def test_chunk_store_counts_embedded(stores):
    _, chk = stores
    chk.replace_for_source("s1", [
        {"text": "plain"},
        {"text": "vec", "embedding": [0.1, 0.2], "embedding_model": "m"},
    ])
    assert chk.counts() == (2, 1)
    assert chk.delete_for_source("s1") == 2


# ── indexing ─────────────────────────────────────────────────────────────────


def test_index_source_writes_structure_aware_rows(stores):
    _, chk = stores
    n = kb_index.index_source("src-1", MD, embed=False)
    assert n >= 2
    rows = chk.get_by_source("src-1")
    assert any("实施例 1" in r.heading_path for r in rows)
    assert any("盐雾" in r.text for r in rows)


def test_index_source_disabled_flag(monkeypatch, stores):
    monkeypatch.setenv("FORMUMIND_KB_V2_ENABLED", "false")
    get_settings.cache_clear()
    assert kb_index.index_source("src-1", MD) == 0
    assert stores[1].counts() == (0, 0)


def test_index_source_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("app.db.chunk_store.get_chunk_store", boom)
    assert kb_index.index_source("src-1", MD) == 0


def test_ingest_persist_populates_kb(stores):
    from app.services.ingestion import ingest_text

    src, chk = stores
    outcome = ingest_text(MD, title="防腐专利", persist=True)
    assert outcome.source_id is not None
    rows = chk.get_by_source(outcome.source_id)
    assert rows, "persisted ingest must create KB chunk rows"


def test_reindex_all_backfills(stores):
    src, chk = stores
    sid = src.create(filename="a.md", title="a", source_kind="local",
                     full_text=MD, content_hash="h1")
    assert chk.counts() == (0, 0)  # created directly, not via ingest hook
    result = kb_index.reindex_all(embed=False)
    assert result["reindexed_sources"] == 1
    assert result["total_chunks"] >= 2
    assert chk.get_by_source(sid)


# ── retrieval ────────────────────────────────────────────────────────────────


def test_search_chunks_keyword_mode(stores):
    src, _ = stores
    sid = src.create(filename="p.md", title="防腐底漆专利", source_kind="patent",
                     full_text=MD, content_hash="h2")
    kb_index.index_source(sid, MD, embed=False)
    hits = kb_index.search_chunks("磷酸锌 盐雾", k=3)
    assert hits
    top = hits[0]
    assert top.identifier.startswith(f"kb:{sid}#c")
    assert "防腐底漆专利" in top.title
    assert "磷酸锌" in top.snippet or "盐雾" in top.snippet
    assert top.source == "patent"


def test_search_chunks_embedding_mode(stores, monkeypatch):
    src, chk = stores
    sid = src.create(filename="p.md", title="T", source_kind="literature",
                     full_text="x", content_hash="h3")
    chk.replace_for_source(sid, [
        {"text": "epoxy anticorrosion primer", "embedding": [1.0, 0.0], "embedding_model": "m"},
        {"text": "polyurethane topcoat gloss", "embedding": [0.0, 1.0], "embedding_model": "m"},
    ])
    monkeypatch.setattr(kb_index, "_embed_texts", lambda texts: [[0.9, 0.1]])
    hits = kb_index.search_chunks("防腐", k=1)
    assert len(hits) == 1
    assert "epoxy" in hits[0].snippet


def test_search_chunks_empty_kb_and_disabled(monkeypatch, stores):
    assert kb_index.search_chunks("anything") == []
    monkeypatch.setenv("FORMUMIND_KB_V2_ENABLED", "false")
    get_settings.cache_clear()
    assert kb_index.search_chunks("anything") == []


def test_kb_stats_counts(stores):
    src, _ = stores
    sid = src.create(filename="p.md", title="T", source_kind="web",
                     full_text=MD, content_hash="h4")
    kb_index.index_source(sid, MD, embed=False)
    stats = kb_index.kb_stats()
    assert stats["sources"] == 1
    assert stats["sources_by_kind"] == {"web": 1}
    assert stats["chunks"] >= 2
    assert stats["embedded_chunks"] == 0


# ── chat + API integration ───────────────────────────────────────────────────


def _client() -> TestClient:
    from app.main import app

    return TestClient(app)


def test_chat_merges_kb_chunks(monkeypatch, stores):
    kb_hit = Evidence(source="patent", identifier="kb:s#c0",
                      title="专利 · 实施例 1", snippet="磷酸锌十五份，盐雾七百二十小时。",
                      relevance=0.9)
    monkeypatch.setattr("app.services.kb_index.search_chunks", lambda q, k=6: [kb_hit])
    resp = _client().post("/api/chat", json={"question": "磷酸锌用量多少？", "sources": []})
    assert resp.status_code == 200
    data = resp.json()
    assert data["kb_chunks_used"] == 1
    assert any(c["identifier"] == "kb:s#c0" for c in data["citations"])


def test_chat_skips_duplicate_kb_identifiers(monkeypatch, stores):
    kb_hit = Evidence(source="patent", identifier="dup#0", title="t", snippet="s", relevance=0.9)
    monkeypatch.setattr("app.services.kb_index.search_chunks", lambda q, k=6: [kb_hit])
    resp = _client().post("/api/chat", json={
        "question": "q",
        "sources": [{"source": "local", "identifier": "dup#0", "title": "t",
                     "snippet": "s", "relevance": 0.5}],
    })
    assert resp.status_code == 200
    assert resp.json()["kb_chunks_used"] == 0


def test_chat_unchanged_when_kb_disabled(monkeypatch, stores):
    monkeypatch.setenv("FORMUMIND_KB_V2_ENABLED", "false")
    get_settings.cache_clear()
    called = []
    monkeypatch.setattr("app.services.kb_index.search_chunks",
                        lambda q, k=6: called.append(q) or [])
    resp = _client().post("/api/chat", json={"question": "q", "sources": []})
    assert resp.status_code == 200
    assert called == []
    assert resp.json()["kb_chunks_used"] == 0


def test_kb_api_endpoints(monkeypatch, stores):
    src, _ = stores
    sid = src.create(filename="p.md", title="防腐底漆专利", source_kind="patent",
                     full_text=MD, content_hash="h5")
    kb_index.index_source(sid, MD, embed=False)

    client = _client()
    stats = client.get("/api/kb/stats")
    assert stats.status_code == 200
    assert stats.json()["chunks"] >= 2

    found = client.get("/api/kb/search", params={"q": "盐雾", "k": 3})
    assert found.status_code == 200
    assert found.json()["results"]

    re_resp = client.post("/api/kb/reindex", params={"embed": "false"})
    assert re_resp.status_code == 200
    assert re_resp.json()["reindexed_sources"] == 1


def test_kb_reindex_conflict_when_disabled(monkeypatch, stores):
    monkeypatch.setenv("FORMUMIND_KB_V2_ENABLED", "false")
    get_settings.cache_clear()
    resp = _client().post("/api/kb/reindex")
    assert resp.status_code == 409

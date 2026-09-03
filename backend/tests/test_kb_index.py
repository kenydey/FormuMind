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
    # The stored rows claim model "m", so the query has to come from "m" too —
    # otherwise `comparable_embedding` rightly refuses to score them together.
    monkeypatch.setattr(kb_index, "_embed_model_name", lambda: "m")
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
    monkeypatch.setattr("app.services.kb_index.search_chunks", lambda q, k=6, **_: [kb_hit])
    resp = _client().post("/api/chat", json={"question": "磷酸锌用量多少？", "sources": []})
    assert resp.status_code == 200
    data = resp.json()
    assert data["kb_chunks_used"] == 1
    assert any(c["identifier"] == "kb:s#c0" for c in data["citations"])


def test_chat_skips_duplicate_kb_identifiers(monkeypatch, stores):
    kb_hit = Evidence(source="patent", identifier="dup#0", title="t", snippet="s", relevance=0.9)
    monkeypatch.setattr("app.services.kb_index.search_chunks", lambda q, k=6, **_: [kb_hit])
    resp = _client().post("/api/chat", json={
        "question": "q",
        "sources": [{"source": "local", "identifier": "dup#0", "title": "t",
                     "snippet": "s", "relevance": 0.5}],
    })
    assert resp.status_code == 200
    assert resp.json()["kb_chunks_used"] == 0


def test_chat_unchanged_when_kb_disabled(monkeypatch, stores):
    # v6 起 KG 默认开启且优先于 KB 分支（chat.py:115），KG 语义检索会调
    # search_chunks。验证「检索完全禁用时 KB 零调用」须同时关 KG。
    monkeypatch.setenv("FORMUMIND_KB_V2_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "false")
    get_settings.cache_clear()
    called = []
    monkeypatch.setattr("app.services.kb_index.search_chunks",
                        lambda q, k=6, project_id=None: called.append(q) or [])
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
    # prune_source_fulltext=True（默认）：index_source 切块后已清空 full_text，
    # reindex 找不到全文 → 跳过（"入库后删源文档"的预期代价，见 test_ingest_prune）。
    assert re_resp.json()["reindexed_sources"] == 0


def test_kb_reindex_works_when_prune_disabled(monkeypatch, stores):
    """关闭 prune 时 full_text 保留，reindex 可正常重建（功能可回退）。"""
    monkeypatch.setattr(get_settings(), "prune_source_fulltext", False, raising=False)
    src, _ = stores
    sid = src.create(filename="p.md", title="防腐底漆专利", source_kind="patent",
                     full_text=MD, content_hash="h6")
    kb_index.index_source(sid, MD, embed=False)
    re_resp = _client().post("/api/kb/reindex", params={"embed": "false"})
    assert re_resp.status_code == 200
    assert re_resp.json()["reindexed_sources"] == 1


def test_kb_reindex_conflict_when_disabled(monkeypatch, stores):
    monkeypatch.setenv("FORMUMIND_KB_V2_ENABLED", "false")
    get_settings.cache_clear()
    resp = _client().post("/api/kb/reindex")
    assert resp.status_code == 409


# ── embedding comparability guards ───────────────────────────────────────────
#
# The scoring loops used to say `zip(query_vec, c.embedding)`, which truncates
# to the shorter vector instead of refusing to compare. A 384-dim query against
# 1536-dim rows produced confident-looking scores over a prefix of two
# unrelated spaces — no exception, no log line, just wrong ordering. These pin
# the refusal.
#
# Note the existing fakes here return fixed 2-dim vectors and ignore their
# input, so they would happily "pass" against a remote 1536-dim API. That is
# why the correspondence test below asserts on the arguments, not just the
# result.


def _multi_chunk_text() -> str:
    """Text that reliably survives the >30-char filter as several chunks.

    Short paragraphs get merged into one chunk, which would make a
    count-mismatch fixture silently self-consistent and the test vacuous.
    """
    body = "环氧树脂与磷酸锌协同防腐蚀机理研究，颜料体积浓度控制在百分之三十五。" * 3
    return "\n\n".join(f"## 段落 {i}\n\n{body}" for i in range(4))


def test_mixed_dimensions_are_not_scored_by_truncation(stores, monkeypatch):
    """A row of another dimension must not out-rank a genuinely similar one."""
    src, chk = stores
    sid = src.create(filename="p.md", title="T", source_kind="literature",
                     full_text="x", content_hash="hdim")
    chk.replace_for_source(sid, [
        # Truncated to 2 dims this scores 1.0 — higher than the real match —
        # purely because its first component happens to be large.
        {"text": "unrelated filler text about packaging logistics",
         "embedding": [1.0, 0.0, 0.5, 0.5], "embedding_model": "m"},
        {"text": "epoxy anticorrosion primer", "embedding": [0.9, 0.1],
         "embedding_model": "m"},
    ])
    monkeypatch.setattr(kb_index, "_embed_texts", lambda texts: [[1.0, 0.0]])
    monkeypatch.setattr(kb_index, "_embed_model_name", lambda: "m")

    hits = kb_index.search_chunks("防腐", k=2)
    assert hits, "the corpus must still be searchable"
    # The 4-dim row is keyword-scored, so it cannot win on a bogus cosine.
    assert "epoxy" in hits[0].snippet


def test_vectors_from_another_model_are_not_compared(stores, monkeypatch):
    """Same dimension, different model — equal length is not comparability."""
    src, chk = stores
    sid = src.create(filename="p.md", title="T", source_kind="literature",
                     full_text="x", content_hash="hmodel")
    chk.replace_for_source(sid, [
        {"text": "alpha", "embedding": [1.0, 0.0], "embedding_model": "old-model"},
    ])
    monkeypatch.setattr(kb_index, "_embed_texts", lambda texts: [[1.0, 0.0]])
    monkeypatch.setattr(kb_index, "_embed_model_name", lambda: "new-model")

    chunk = chk.get_by_source(sid)[0]
    assert kb_index.comparable_embedding(chunk, 2, "new-model") is False
    assert kb_index.comparable_embedding(chunk, 2, "old-model") is True


def test_legacy_rows_without_a_model_name_stay_searchable(stores):
    """NULL predates the column; excluding those rows would gut old corpora."""
    src, chk = stores
    sid = src.create(filename="p.md", title="T", source_kind="literature",
                     full_text="x", content_hash="hnull")
    chk.replace_for_source(sid, [{"text": "legacy", "embedding": [1.0, 0.0]}])

    chunk = chk.get_by_source(sid)[0]
    assert kb_index.comparable_embedding(chunk, 2, "any-model") is True
    # ...but a dimension mismatch still disqualifies them.
    assert kb_index.comparable_embedding(chunk, 3, "any-model") is False


def test_embedding_count_mismatch_drops_the_batch(stores, monkeypatch):
    """Binding N vectors to M rows would give chunks their neighbour's meaning.

    Silently zipping to the shorter list is worse than having no vectors: the
    rows still look embedded, and every row after the mismatch is wrong.
    """
    src, chk = stores
    text = _multi_chunk_text()
    sid = src.create(filename="p.md", title="T", source_kind="literature",
                     full_text=text, content_hash="hcount")
    # One vector back for several chunks.
    monkeypatch.setattr(kb_index, "_embed_texts", lambda texts: [[1.0, 0.0]])

    kb_index.index_source(sid, text)
    assert len(chk.get_by_source(sid)) > 1, "fixture must produce a real mismatch"

    rows = chk.get_by_source(sid)
    assert rows, "chunks must still be written"
    assert all(r.embedding is None for r in rows), "no row may carry a guessed vector"


def test_embed_texts_receives_every_chunk_it_is_asked_about(stores, monkeypatch):
    """The correspondence the other fakes never check.

    Existing fakes return a constant and ignore their input, so a real API
    returning a different count or order would not be caught by them.
    """
    src, chk = stores
    text = _multi_chunk_text()
    sid = src.create(filename="p.md", title="T", source_kind="literature",
                     full_text=text, content_hash="hcorr")
    seen: list[list[str]] = []

    def fake_embed(texts):
        seen.append(list(texts))
        return [[float(i), 0.0] for i in range(len(texts))]

    monkeypatch.setattr(kb_index, "_embed_texts", fake_embed)
    kb_index.index_source(sid, text)

    assert seen, "_embed_texts was never called"
    rows = chk.get_by_source(sid)
    assert len(seen[0]) == len(rows)
    # Every stored row's text was actually submitted, in the same order.
    assert seen[0] == [r.text for r in rows]


def test_stats_reports_stale_when_vectors_belong_to_another_model(stores, monkeypatch):
    """`semantic` over an uncomparable corpus was the last green-over-broken."""
    src, chk = stores
    sid = src.create(filename="p.md", title="T", source_kind="literature",
                     full_text="x", content_hash="hstale")
    chk.replace_for_source(sid, [
        {"text": "alpha", "embedding": [1.0, 0.0], "embedding_model": "old-model"},
        {"text": "beta", "embedding": [0.0, 1.0], "embedding_model": "old-model"},
    ])
    monkeypatch.setattr(kb_index, "_embed_model_name", lambda: "new-model")

    stats = kb_index.kb_stats()
    assert stats["stale_chunks"] == 2
    assert stats["vector_mode"] == "stale"
    assert "重建索引" in stats["vector_hint"]


# ── configurable embedding model ─────────────────────────────────────────────


def test_embedding_model_defaults_to_the_shipped_one(monkeypatch):
    """Empty setting must behave exactly as before this became configurable."""
    from app.services import rag

    monkeypatch.delenv("FORMUMIND_EMBEDDING_MODEL", raising=False)
    get_settings.cache_clear()
    assert rag.embed_model_name() == rag._EMBED_MODEL
    assert kb_index._embed_model_name() == rag._EMBED_MODEL


def test_embedding_model_setting_is_honoured(monkeypatch):
    from app.services import rag

    monkeypatch.setenv("FORMUMIND_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    get_settings.cache_clear()
    assert rag.embed_model_name() == "BAAI/bge-small-zh-v1.5"
    # kb_index stamps rows with this name, which is what makes vectors from a
    # previous model detectable instead of silently incomparable.
    assert kb_index._embed_model_name() == "BAAI/bge-small-zh-v1.5"


def test_blank_or_whitespace_setting_falls_back(monkeypatch):
    from app.services import rag

    monkeypatch.setenv("FORMUMIND_EMBEDDING_MODEL", "   ")
    get_settings.cache_clear()
    assert rag.embed_model_name() == rag._EMBED_MODEL


def test_switching_the_model_marks_the_existing_corpus_stale(stores, monkeypatch):
    """The whole point of making this configurable safely.

    Rows embedded by the old model must be reported as needing a rebuild, not
    counted towards a green `semantic` badge.
    """
    from app.services import rag

    src, chk = stores
    sid = src.create(filename="p.md", title="T", source_kind="patent",
                     full_text="x", content_hash="hswitch")
    chk.replace_for_source(sid, [
        {"text": "旧模型向量", "embedding": [1.0, 0.0], "embedding_model": rag._EMBED_MODEL},
    ])

    monkeypatch.setenv("FORMUMIND_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    get_settings.cache_clear()

    stats = kb_index.kb_stats()
    assert stats["vector_mode"] == "stale"
    assert stats["stale_chunks"] == 1
    assert "重建索引" in stats["vector_hint"]


def test_stats_exposes_the_pending_structure_count(stores):
    """Pinned directly because `kb_stats` never raises — it degrades to a
    partial dict, so a bug inside it shows up as a *missing key* rather than an
    error. Asserting the key exists is what turns that back into a failure."""
    src, _ = stores
    sid = src.create(filename="p.md", title="T", source_kind="patent",
                     full_text=MD, content_hash="hpend")
    kb_index.index_source(sid, MD, embed=False)

    stats = kb_index.kb_stats()
    assert "products_pending_structure" in stats
    assert isinstance(stats["products_pending_structure"], int)

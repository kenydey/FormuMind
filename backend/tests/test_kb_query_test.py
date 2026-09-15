"""Tests for KB query-test probe and scored hybrid search."""

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


def test_hybrid_search_scored_matches_unscored_order(stores, monkeypatch):
    src, chk = stores
    sid1 = src.create(
        filename="d1.md",
        title="防腐涂料研究",
        source_kind="local",
        full_text="环氧树脂防腐底漆磷酸锌防锈颜料",
        content_hash="h1",
    )
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
    monkeypatch.setattr(
        "app.services.kb_index._embed_texts",
        lambda texts, model_name=None: [[0.9, 0.1]],
    )

    from app.services.hybrid_search import hybrid_search, hybrid_search_scored

    unscored = hybrid_search("防腐涂料 环氧 磷酸锌", top_k=5)
    scored = hybrid_search_scored("防腐涂料 环氧 磷酸锌", top_k=5)
    assert unscored and scored
    assert [c.id for c in unscored] == [s.chunk.id for s in scored]
    assert all(s.hybrid_score is not None for s in scored)
    assert scored[0].hybrid_score >= scored[-1].hybrid_score


def test_query_test_hybrid_returns_scores(stores):
    client = _client()
    resp = client.post(
        "/api/kb/ingest",
        json={
            "text": "硅烷偶联剂可用于金属表面处理，改善涂层附着力。磷化液常用于前处理。",
            "title": "表面处理",
        },
    )
    assert resp.status_code == 200, resp.text

    resp2 = client.post(
        "/api/kb/query-test",
        json={"query": "硅烷偶联剂 磷化液", "mode": "hybrid", "top_k": 5, "alpha": 0.3},
    )
    assert resp2.status_code == 200, resp2.text
    data = resp2.json()
    assert data["mode"] == "hybrid"
    assert data["hits"], data
    hit = data["hits"][0]
    assert hit["bm25_score"] is not None or hit["cosine_score"] is not None or hit["hybrid_score"] is not None
    assert "hybrid_score" in hit
    # hybrid scores should be monotone non-increasing
    scores = [h["hybrid_score"] for h in data["hits"] if h["hybrid_score"] is not None]
    assert scores == sorted(scores, reverse=True)


def test_query_test_hybrid_rerank_degrades(stores, monkeypatch):
    client = _client()
    ing = client.post(
        "/api/kb/ingest",
        json={
            "text": (
                "水性环氧涂料在盐雾试验中表现优异，可用于金属表面防腐。"
                "中性盐雾试验是常用的评价方法，盐雾时间常作为关键指标。"
            ),
            "title": "盐雾",
        },
    )
    assert ing.status_code == 200, ing.text
    assert ing.json().get("chunk_count", 0) >= 1

    def _boom(*_a, **_k):
        raise RuntimeError("no llm")

    monkeypatch.setattr("app.services.llm.complete_json", _boom)

    resp = client.post(
        "/api/kb/query-test",
        json={
            "query": "水性环氧 盐雾",
            "mode": "hybrid_rerank",
            "top_k": 3,
            "rerank": True,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["params"]["rerank_applied"] is False
    assert data["hits"], data  # fallback hybrid hits still present
    assert data.get("warning")


def test_golden_questions_endpoint(stores):
    client = _client()
    resp = client.get("/api/kb/golden-questions")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 3
    assert rows[0]["question"]
    assert rows[0]["expected_keywords"]


def test_golden_eval_run(stores):
    client = _client()
    from app.resources.golden_retrieval import sample_documents

    for doc in sample_documents:
        r = client.post("/api/kb/ingest", json={"text": doc["text"], "title": doc["title"]})
        assert r.status_code == 200, r.text

    resp = client.post(
        "/api/kb/golden-eval/run",
        json={"mode": "hybrid", "top_k": 3, "alpha": 0.3},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] >= 3
    assert "passed" in data
    assert len(data["results"]) == data["total"]

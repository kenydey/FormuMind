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
    assert "mrr" in data and isinstance(data["mrr"], (int, float))
    assert "recall_at_k" in data and isinstance(data["recall_at_k"], (int, float))
    assert 0.0 <= data["mrr"] <= 1.0
    assert 0.0 <= data["recall_at_k"] <= 1.0
    assert data["recall_at_k"] == pytest.approx(data["passed"] / data["total"])


def test_query_test_keyword_mode_is_pure_keyword(stores, monkeypatch):
    """P2: keyword 探针强制纯关键词 —— 有 embedding 时也不走 cosine，
    hybrid_score/cosine_score 必须为 None。"""
    client = _client()
    r = client.post(
        "/api/kb/ingest",
        json={
            "text": "硅烷偶联剂可用于金属表面处理，改善涂层附着力。磷化液常用于前处理。",
            "title": "表面处理",
        },
    )
    assert r.status_code == 200, r.text

    # 即使 embedding 可用，keyword 模式也不计算 cosine
    resp = client.post(
        "/api/kb/query-test",
        json={"query": "硅烷偶联剂", "mode": "keyword", "top_k": 5},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "keyword"
    assert data["hits"], data
    for hit in data["hits"]:
        assert hit["cosine_score"] is None, "keyword 模式不应有 cosine 分"
        assert hit["hybrid_score"] is None, "keyword 模式不应有 hybrid 分"


def test_hybrid_entity_boost_lifts_entity_chunk(stores, monkeypatch):
    """P2: kb_hybrid_entity_boost=1 时，含查询 CAS 的 chunk 在融合前获得
    +0.3 加性 boost（legacy _entity_boost 的 CAS 档）；关闭时无加成。"""
    from app.config import get_settings
    from app.services.hybrid_search import hybrid_search_scored

    src, chk = stores
    sid_a = src.create(filename="a.md", title="a", source_kind="local",
                       full_text="x", content_hash="ha")
    chk.replace_for_source(sid_a, [{
        # The CAS lives in the metadata only, so what is tested is the boost. The chunk still has to share a word with the
        # query to be a candidate at all: it used to get in through the blank before "0.5%", which the tokenizer
        # treated as a term (a match of every query containing a space with every text containing one).
        "text": "该硅烷助剂可改善涂层附着力，添加量为总配方的 0.5%。",
        "meta": {"chem": [{"type": "cas", "value": "2530-83-8"}]},
    }])
    sid_b = src.create(filename="b.md", title="b", source_kind="local",
                       full_text="y", content_hash="hb")
    chk.replace_for_source(sid_b, [{
        "text": "硅烷偶联剂在金属表面处理中作为附着力促进剂，CAS 2530-83-8 "
                "的水解缩合形成硅氧烷网络。",
    }])

    def score_of(sid: str, scored) -> float | None:
        for sc in scored:
            if sc.chunk.source_id == sid:
                return sc.hybrid_score
        return None

    q = "CAS 2530-83-8 硅烷偶联剂附着力"
    monkeypatch.setenv("FORMUMIND_KB_HYBRID_ENTITY_BOOST", "0")
    get_settings.cache_clear()
    control = hybrid_search_scored(q, top_k=5)
    monkeypatch.setenv("FORMUMIND_KB_HYBRID_ENTITY_BOOST", "1")
    get_settings.cache_clear()
    treatment = hybrid_search_scored(q, top_k=5)
    get_settings.cache_clear()

    ca, ta = score_of(sid_a, control), score_of(sid_a, treatment)
    assert ca is not None and ta is not None
    # 加性 boost：treatment = control + 0.3（CAS 档），融合是线性的
    assert ta == pytest.approx(ca + 0.3, abs=1e-6)
    # 无 chem 元数据的 chunk 不受加成影响（分量不变）
    cb = score_of(sid_b, control)
    tb = score_of(sid_b, treatment)
    if cb is not None and tb is not None:
        assert tb == pytest.approx(cb, abs=1e-6)



def test_probe_rerank_reads_kb_recommend_switch(stores, monkeypatch):
    """P1-12: hybrid_rerank 探针默认读 kb_recommend_rerank_enabled，
    不再读 search_rerank_enabled（两者默认行为不一致）。"""
    from app.config import get_settings

    client = _client()
    r = client.post(
        "/api/kb/ingest",
        json={"text": "硅烷偶联剂改善涂层附着力。", "title": "t"},
    )
    assert r.status_code == 200, r.text

    # search_rerank 开但 kb_recommend_rerank 关 → 不重排
    monkeypatch.setenv("FORMUMIND_SEARCH_RERANK_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_KB_RECOMMEND_RERANK_ENABLED", "false")
    get_settings.cache_clear()
    resp = client.post(
        "/api/kb/query-test",
        json={"query": "硅烷偶联剂", "mode": "hybrid_rerank", "top_k": 3},
    )
    get_settings.cache_clear()
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["params"]["rerank_applied"] is False
    assert "kb_recommend_rerank_enabled" in (data["warning"] or "")

    # kb_recommend_rerank 开 → 尝试重排（无 LLM key 时 fail-open 降级）
    monkeypatch.setenv("FORMUMIND_KB_RECOMMEND_RERANK_ENABLED", "true")
    get_settings.cache_clear()
    resp = client.post(
        "/api/kb/query-test",
        json={"query": "硅烷偶联剂", "mode": "hybrid_rerank", "top_k": 3},
    )
    get_settings.cache_clear()
    assert resp.status_code == 200, resp.text

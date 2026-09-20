"""Quality-gate drop counters for probe / Hub observability."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.main import app
from app.services import kb_index
from app.services.kb_retrieval_gate import (
    gate_drop_stats,
    record_gate_drop,
    reset_gate_drop_stats,
)


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
    reset_gate_drop_stats()
    yield
    reset_gate_drop_stats()
    get_settings.cache_clear()


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_store_mod
    import app.db.database as db_mod
    import app.db.source_store as source_store_mod
    from app.db.chunk_store import ChunkStore
    from app.db.source_store import SourceStore

    engine = make_engine(f"sqlite:///{tmp_path}/kb_gate_ctr.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)
    return src, chk


def test_record_gate_drop_increments_nested():
    record_gate_drop("retrieval", "blocked_domain", 2)
    record_gate_drop("ingest", "garbage_snippet")
    stats = gate_drop_stats()
    assert stats["retrieval"]["blocked_domain"] == 2
    assert stats["ingest"]["garbage_snippet"] == 1
    assert stats["retrieval"]["wiki_track"] == 0


def test_hybrid_retrieval_increments_and_probe_reports_delta(stores, monkeypatch):
    src, chk = stores
    good = src.create(
        filename="good.md",
        title="防腐涂料研究",
        source_kind="local",
        full_text=GOOD_TEXT,
        content_hash="good",
        origin_url=None,
    )
    dirty = src.create(
        filename="dirty.md",
        title="Buy epoxy",
        source_kind="web",
        full_text=GOOD_TEXT,
        content_hash="dirty",
        origin_url="https://www.alibaba.com/product/epoxy",
    )
    emb = [1.0, 0.0]
    chk.replace_for_source(
        good,
        [{"text": GOOD_TEXT, "embedding": emb, "embedding_model": "m"}],
    )
    chk.replace_for_source(
        dirty,
        [
            {
                "text": "环氧 防腐 底漆 Buy epoxy paint cheap bulk wholesale listing text.",
                "embedding": emb,
                "embedding_model": "m",
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.kb_index._embed_texts",
        lambda texts, model_name=None: [[0.9, 0.1] for _ in texts],
    )
    monkeypatch.setattr("app.services.kb_retrieval_gate._wiki_source_ids", lambda: set())

    before = gate_drop_stats()["retrieval"]["blocked_domain"]
    client = TestClient(app)
    body = client.post(
        "/api/kb/query-test",
        json={"query": "环氧 防腐 底漆", "mode": "hybrid", "top_k": 5, "alpha": 0.5},
    ).json()
    assert body["gate_drops"]["retrieval"]["blocked_domain"] >= 1
    assert gate_drop_stats()["retrieval"]["blocked_domain"] >= before + 1
    assert body["gate_drops_total"]["retrieval"]["blocked_domain"] >= 1
    source_ids = {h.get("source_id") for h in body["hits"]}
    assert dirty not in source_ids


def test_ingest_blocked_increments_and_stats(stores):
    src, _chk = stores
    dirty = src.create(
        filename="alibaba.md",
        title="market",
        source_kind="web",
        full_text=GOOD_TEXT,
        content_hash="d",
        origin_url="https://alibaba.com/item/1",
    )
    before = gate_drop_stats()["ingest"]["blocked_domain"]
    assert kb_index.index_source(dirty, GOOD_TEXT, embed=False) == 0
    assert gate_drop_stats()["ingest"]["blocked_domain"] == before + 1

    client = TestClient(app)
    stats = client.get("/api/kb/stats").json()
    assert stats["quality_gate_drops"]["ingest"]["blocked_domain"] >= 1


def test_filter_disabled_skips_counting(stores, monkeypatch):
    monkeypatch.setenv("FORMUMIND_CONTENT_FILTER_ENABLED", "false")
    get_settings.cache_clear()
    src, _chk = stores
    dirty = src.create(
        filename="alibaba.md",
        title="market",
        source_kind="web",
        full_text=GOOD_TEXT,
        content_hash="off",
        origin_url="https://www.alibaba.com/x",
    )
    n = kb_index.index_source(dirty, GOOD_TEXT, embed=False)
    assert n >= 1
    assert gate_drop_stats()["ingest"]["blocked_domain"] == 0

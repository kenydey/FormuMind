"""B-9: KB 健康仪表盘 v1（parser 分布 / embedding 覆盖率 / 空文档率）。"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.source_store import SourceStore


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

    engine = make_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)
    return src, chk


def _seed(stores):
    src, chk = stores
    d1 = src.create(
        filename="a.pdf", title="A", source_kind="local",
        full_text="epoxy " * 50, content_hash="h1", parser="hybrid",
    )
    d2 = src.create(
        filename="b.pdf", title="B", source_kind="local",
        full_text="zinc " * 50, content_hash="h2", parser="pypdf",
    )
    d3 = src.create(
        filename="c.txt", title="C", source_kind="local",
        full_text="plain " * 50, content_hash="h3", parser=None,
    )
    chk.replace_for_source(
        d1,
        [
            {"text": "chunk one", "embedding": [0.1, 0.2], "embedding_model": "m"},
            {"text": "chunk two"},  # 未向量化
        ],
    )
    chk.replace_for_source(d2, [{"text": "chunk three"}])
    # d3：零切块文档
    return d1, d2, d3


def test_kb_health_snapshot(stores):
    from app.services.kb_index import kb_health_snapshot

    _seed(stores)
    snap = kb_health_snapshot()
    assert snap["available"] is True
    assert snap["total_documents"] == 3
    assert snap["total_chunks"] == 3
    assert snap["parser_distribution"] == {"hybrid": 1, "pypdf": 1, "unknown": 1}
    cov = snap["embedding_coverage"]
    assert cov["embedded_chunks"] == 1
    assert cov["total_chunks"] == 3
    assert cov["rate"] == pytest.approx(1 / 3)
    empty = snap["empty_doc_rate"]
    assert empty["empty_documents"] == 1
    assert empty["rate"] == pytest.approx(1 / 3)


def test_kb_health_empty_db(stores):
    from app.services.kb_index import kb_health_snapshot

    snap = kb_health_snapshot()
    assert snap["available"] is True
    assert snap["total_documents"] == 0
    assert snap["embedding_coverage"]["rate"] is None
    assert snap["empty_doc_rate"]["rate"] is None


def test_kb_health_fail_open(stores, monkeypatch):
    import app.services.kb_index as kb_index

    def _boom():
        raise RuntimeError("db gone")

    monkeypatch.setattr(kb_index, "_coverage_session_factory", _boom)
    assert kb_index.kb_health_snapshot() == {"available": False}


def test_ops_endpoint(stores):
    from app.api.ops import kb_health

    _seed(stores)
    body = kb_health()
    assert body["available"] is True
    assert body["parser_distribution"]["hybrid"] == 1
    assert body["empty_doc_rate"]["empty_documents"] == 1

"""Task 2.4: Full ingest endpoint — idempotent chunking + outbox records."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory

PAGED_MD = """<!-- page:1 -->
# 防腐涂料研究

环氧树脂作为主要成膜物质，具有优异的附着力和耐化学性。

添加磷酸锌作为防锈颜料可显著提升盐雾耐受时间。

<!-- page:2 -->
## 工艺参数

固化温度控制在80-120°C范围内可获得最佳性能。

涂膜厚度建议控制在25-35微米。
"""


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
    # Make default_session_factory return the same temp-DB factory
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)
    return src, chk


def _client() -> TestClient:
    from app.main import app

    return TestClient(app)


# ── ingest endpoint tests ─────────────────────────────────────────────────────


def test_full_ingest_produces_chunks(stores):
    """POST /api/kb/ingest → document_chunks rows created."""
    _, chk = stores

    client = _client()
    resp = client.post(
        "/api/kb/ingest",
        json={"text": PAGED_MD, "title": "防腐涂料研究"},
    )
    assert resp.status_code == 200, f"unexpected status: {resp.status_code}, body: {resp.json()}"
    data = resp.json()
    assert data["chunk_count"] > 0, f"expected chunks, got {data}"
    assert data["source_id"], "source_id must not be empty"
    assert data["status"] == "ok"

    # Verify chunks in the DB
    rows = chk.get_by_source(data["source_id"])
    assert len(rows) == data["chunk_count"], (
        f"DB chunk count {len(rows)} != response chunk_count {data['chunk_count']}"
    )
    assert len(rows) > 0

    # At least one chunk has offset data
    chunks_with_offset = [r for r in rows if r.offset_start is not None and r.offset_end is not None]
    assert len(chunks_with_offset) >= 1, "expected at least one chunk with offset data"


def test_ingest_idempotent(stores):
    """Same source_id ingested twice → chunk count unchanged (second call returns 0)."""
    _, chk = stores

    client = _client()

    # First ingest
    resp1 = client.post(
        "/api/kb/ingest",
        json={"text": PAGED_MD, "title": "Idempotent Test"},
    )
    assert resp1.status_code == 200
    sid = resp1.json()["source_id"]
    count1 = resp1.json()["chunk_count"]
    assert count1 > 0

    # Second ingest with same source_id
    resp2 = client.post(
        "/api/kb/ingest",
        json={"text": PAGED_MD, "source_id": sid, "title": "Idempotent Test"},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["source_id"] == sid
    assert data2["chunk_count"] == 0, (
        f"idempotent second ingest must return chunk_count=0, got {data2['chunk_count']}"
    )
    assert data2["status"] == "ok"

    # Chunk count in DB must be unchanged
    rows = chk.get_by_source(sid)
    assert len(rows) == count1, (
        f"idempotent call changed chunk count from {count1} to {len(rows)}"
    )

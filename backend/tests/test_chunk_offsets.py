"""Task 2.1: chunk offset output — retrieval results carry page/paragraph/offset fields."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.services import kb_index


PAGED_MD = """<!-- page:1 -->
# 防腐涂料研究

环氧树脂作为主要成膜物质，具有优异的附着力和耐化学性。

添加磷酸锌作为防锈颜料可显著提升盐雾耐受时间。

<!-- page:2 -->
## 工艺参数

固化温度控制在80-120°C范围内可获得最佳性能。

涂膜厚度建议控制在25-35微米。
"""

MULTI_PARA_MD = """# 引言

本研究旨在开发一种新型水性防腐涂料体系，以替代传统溶剂型涂料。

水性体系具有低VOC排放的环保优势，符合日益严格的环保法规要求。

树脂选择是配方设计的核心，需要平衡防腐性能与施工可行性。

# 实验方法

基材采用Q235碳钢板，经喷砂处理至Sa 2.5级后待用。

将环氧树脂与固化剂按比例混合，机械搅拌15分钟后静置消泡。

采用空气喷涂方式将涂料均匀喷涂于基材表面，控制干膜厚度为30±5微米。

# 结果与讨论

盐雾试验结果显示，含磷酸锌10%的配方在720小时后仅有轻微起泡。

电化学阻抗谱分析表明涂层在浸泡初期具有较高的阻抗模值。

最优配方在耐盐雾、附着力和耐水性三项指标上均达到设计要求。
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


def _client() -> TestClient:
    from app.main import app

    return TestClient(app)


def _ingest_and_get_source_id(stores, text: str, title: str) -> str:
    """Ingest text and return the source_id."""
    from app.services.ingestion import ingest_text

    outcome = ingest_text(text, title=title, persist=True)
    assert outcome.source_id is not None, "ingest must return a source_id"
    return outcome.source_id


# ── chunk offset retrieval tests ──────────────────────────────────────────────


def test_chunk_has_page_offset(stores):
    """Ingest a page-marked document, then retrieved chunks carry page >= 0."""
    src, chk = stores
    source_id = _ingest_and_get_source_id(stores, PAGED_MD, "防腐涂料研究")

    client = _client()
    resp = client.get(f"/api/kb/chunks/by-source/{source_id}")
    assert resp.status_code == 200
    data = resp.json()
    chunks = data["chunks"]
    assert len(chunks) >= 2, f"expected >= 2 chunks, got {len(chunks)}"

    # At least one chunk must have a non-None page >= 0
    pages_with_page = [c for c in chunks if c["page"] is not None]
    assert len(pages_with_page) >= 1, "at least one chunk must have page_no set"
    for c in pages_with_page:
        assert c["page"] >= 0, f"page must be >= 0, got {c['page']}"

    # offset_start and offset_end must be present and reasonable
    for c in chunks:
        assert "offset_start" in c
        assert "offset_end" in c
        if c["offset_start"] is not None and c["offset_end"] is not None:
            assert c["offset_start"] >= 0
            assert c["offset_end"] >= c["offset_start"], (
                f"offset_end ({c['offset_end']}) must be >= offset_start ({c['offset_start']})"
            )


def test_chunk_paragraph_index(stores):
    """Multi-paragraph document: different chunks carry different paragraph values."""
    src, chk = stores
    source_id = _ingest_and_get_source_id(stores, MULTI_PARA_MD, "水性防腐涂料")

    client = _client()
    resp = client.get(f"/api/kb/chunks/by-source/{source_id}")
    assert resp.status_code == 200
    data = resp.json()
    chunks = data["chunks"]
    assert len(chunks) >= 3, f"expected >= 3 chunks, got {len(chunks)}"

    # Collect paragraph values from chunks that have them
    para_values = []
    for c in chunks:
        p = c.get("paragraph")
        if p is not None:
            para_values.append(p)

    assert len(para_values) >= 2, (
        f"expected at least 2 chunks with paragraph values, got {len(para_values)}"
    )
    # Different chunks should have different paragraph values
    unique_paras = set(para_values)
    assert len(unique_paras) >= 2, (
        f"expected multiple distinct paragraph values, got: {unique_paras}"
    )

    # Verify that chunks are returned in order (ord increases)
    ords = [c["ord"] for c in chunks]
    assert ords == sorted(ords), f"chunks should be returned in ord order: {ords}"


def test_chunk_response_schema_structure(stores):
    """Verify all new fields appear in the response schema."""
    src, chk = stores
    source_id = _ingest_and_get_source_id(stores, MULTI_PARA_MD, "schema_test")

    client = _client()
    resp = client.get(f"/api/kb/chunks/by-source/{source_id}")
    assert resp.status_code == 200
    data = resp.json()
    chunks = data["chunks"]
    assert len(chunks) >= 1

    # Every chunk must have all DocumentChunkResponse fields
    required_fields = {"id", "source_id", "ord", "text", "heading_path",
                       "page", "paragraph", "offset_start", "offset_end", "meta"}
    for c in chunks:
        missing = required_fields - set(c.keys())
        assert not missing, f"chunk missing fields: {missing}"
        # ord is always present
        assert isinstance(c["ord"], int)
        # text is always present
        assert isinstance(c["text"], str)
        assert len(c["text"]) > 0


# ── Task 2.2: anchor column tests ─────────────────────────────────────────────

def test_anchor_columns_populated(stores):
    """index_source → raw SQL query must show offset_start/end and paragraph_idx columns populated."""
    from sqlalchemy import create_engine, text

    src, chk = stores
    source_id = _ingest_and_get_source_id(stores, MULTI_PARA_MD, "anchor_test")

    # Go directly to the DB to verify column-level values
    from app.db.database import make_engine, make_session_factory

    engine = stores[0]._session_factory.kw["bind"]
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT offset_start, offset_end, paragraph_idx FROM document_chunks "
                "WHERE source_id = :sid ORDER BY ord"
            ),
            {"sid": source_id},
        ).fetchall()

    assert len(rows) >= 3, f"expected >= 3 chunks, got {len(rows)}"
    populated = 0
    for row in rows:
        os_val, oe_val, pi_val = row
        if os_val is not None and oe_val is not None:
            assert os_val >= 0, f"offset_start must be >= 0, got {os_val}"
            assert oe_val >= os_val, f"offset_end ({oe_val}) >= offset_start ({os_val})"
            populated += 1
        if pi_val is not None:
            assert isinstance(pi_val, int)
    assert populated >= 2, f"expected >= 2 chunks with offset columns populated, got {populated}"


def test_anchor_columns_index_exists(tmp_path, monkeypatch):
    """After running Alembic migrations, ix_document_chunks_source_offset index exists."""
    from pathlib import Path
    from sqlalchemy import create_engine, inspect

    db_url = f"sqlite:///{tmp_path}/anchor_index_test.db"
    monkeypatch.setenv("FORMUMIND_DB_URL", db_url)

    from tests.alembic_helpers import run_upgrade

    run_upgrade(db_url, monkeypatch)

    engine = create_engine(db_url)
    try:
        inspector = inspect(engine)
        indexes = {ix["name"] for ix in inspector.get_indexes("document_chunks")}
        assert "ix_document_chunks_source_offset" in indexes, (
            f"expected ix_document_chunks_source_offset in document_chunks indexes, got {indexes}"
        )
    finally:
        engine.dispose()

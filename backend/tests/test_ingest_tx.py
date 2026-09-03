"""Task 3.3: ingest_document_tx — single-transaction document ingest."""

from __future__ import annotations

import pytest
import uuid as _uuid
from sqlalchemy import func

from app.db.database import Base, make_engine, make_session_factory
from app.db.models import DocumentChunk, SourceDocument, TaskOutbox


PAGED_MD = """<!-- page:1 -->
# 防腐涂料研究

环氧树脂作为主要成膜物质，具有优异的附着力和耐化学性能。
通过添加磷酸锌作为防锈颜料，可显著提升涂层的盐雾耐受时间。
涂膜固化温度应控制在80-120摄氏度范围内，确保交联密度与机械强度。
"""


@pytest.fixture(autouse=True)
def _ensure_kb_enabled(monkeypatch):
    """Test environment doesn't load full config — force KB on."""
    monkeypatch.setenv("FORMUMIND_KB_V2_ENABLED", "true")
    from app.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


# ── helpers ──────────────────────────────────────────────────────────────────

def _doc_count(factory, source_id: str) -> int:
    with factory() as s:
        return s.query(func.count(SourceDocument.id)).filter(
            SourceDocument.id == source_id
        ).scalar() or 0


def _chunk_count(factory, source_id: str) -> int:
    with factory() as s:
        return s.query(func.count(DocumentChunk.id)).filter(
            DocumentChunk.source_id == source_id
        ).scalar() or 0


def _outbox_count(factory, source_id: str) -> int:
    with factory() as s:
        return s.query(func.count(TaskOutbox.id)).filter(
            TaskOutbox.idempotency_key == source_id
        ).scalar() or 0


# ── tests ────────────────────────────────────────────────────────────────────


def test_idempotent_duplicate_returns_zero(factory) -> None:
    """Duplicate ingestion → chunk_count=0, already_existed=True, no DB change."""
    from app.services.ingest_tx import ingest_document_tx

    sid = str(_uuid.uuid4())

    result1 = ingest_document_tx(factory, source_id=sid, text=PAGED_MD, title="Test")
    assert result1.chunk_count > 0
    assert not result1.already_existed
    assert result1.source_id == sid

    chunks_before = _chunk_count(factory, sid)
    outbox_before = _outbox_count(factory, sid)

    result2 = ingest_document_tx(factory, source_id=sid, text=PAGED_MD, title="Test")
    assert result2.chunk_count == 0
    assert result2.already_existed

    # No rows added
    assert _chunk_count(factory, sid) == chunks_before
    assert _outbox_count(factory, sid) == outbox_before


def test_concurrent_race_unique_constraint_wins(factory, monkeypatch) -> None:
    """Simulate concurrent insert → unique constraint → idempotent return 0.

    Monkeypatch the chunk check so the function believes no chunks exist,
    even though another 'request' already wrote them — the unique constraint
    IntegrityError is caught and converted to already_existed=True.
    """
    from app.services.ingest_tx import ingest_document_tx

    sid = str(_uuid.uuid4())

    # First: normal ingest creates chunks
    r1 = ingest_document_tx(factory, source_id=sid, text=PAGED_MD, title="Step 1")
    assert r1.chunk_count > 0

    # Second: monkeypatch the SELECT check to return False, but chunks exist
    import app.db.chunk_store as ck
    orig = ck.ChunkStore.get_by_source

    def _pretend_empty(self, source_id):
        return []

    monkeypatch.setattr(ck.ChunkStore, "get_by_source", _pretend_empty)

    # The function will attempt to write, hit the unique constraint,
    # and should catch it → already_existed=True, chunk_count=0
    r2 = ingest_document_tx(factory, source_id=sid, text=PAGED_MD, title="Concurrent")
    assert r2.chunk_count == 0
    assert r2.already_existed

    # Restore for cleanup
    monkeypatch.setattr(ck.ChunkStore, "get_by_source", orig)


def test_chunk_write_failure_rolls_back_everything(factory, monkeypatch) -> None:
    """Chunk replace_for_source_in raises → no SourceDocument, no chunks, no outbox."""
    from app.services.ingest_tx import ingest_document_tx

    # Monkeypatch replace_for_source_in to blow up
    import app.db.chunk_store as ck
    monkeypatch.setattr(
        ck.ChunkStore, "replace_for_source_in",
        lambda self, session, sid, chunks: (_ for _ in ()).throw(RuntimeError("simulated write failure")),
    )

    sid = str(_uuid.uuid4())

    with pytest.raises(RuntimeError, match="simulated write failure"):
        ingest_document_tx(factory, source_id=sid, text=PAGED_MD, title="Fail")

    # Nothing persisted
    assert _doc_count(factory, sid) == 0
    assert _chunk_count(factory, sid) == 0
    assert _outbox_count(factory, sid) == 0


def test_outbox_failure_rolls_back_everything(factory, monkeypatch) -> None:
    """Outbox enqueue raises → no SourceDocument, no chunks, no outbox."""
    from app.services.ingest_tx import ingest_document_tx

    # Monkeypatch enqueue to blow up (after chunks are written)
    import app.db.outbox_store as ob
    monkeypatch.setattr(
        ob, "enqueue",
        lambda session, *a, **kw: (_ for _ in ()).throw(RuntimeError("simulated outbox failure")),
    )

    sid = str(_uuid.uuid4())

    with pytest.raises(RuntimeError, match="simulated outbox failure"):
        ingest_document_tx(factory, source_id=sid, text=PAGED_MD, title="Fail")

    # Everything rolled back
    assert _doc_count(factory, sid) == 0
    assert _chunk_count(factory, sid) == 0
    assert _outbox_count(factory, sid) == 0


def test_recovery_after_failed_ingest(factory, monkeypatch) -> None:
    """After a failed ingest, retry with same source_id succeeds."""
    from app.services.ingest_tx import ingest_document_tx

    sid = str(_uuid.uuid4())

    # Fail first attempt
    import app.db.chunk_store as ck
    monkeypatch.setattr(
        ck.ChunkStore, "replace_for_source_in",
        lambda self, session, sid2, chunks: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError):
        ingest_document_tx(factory, source_id=sid, text=PAGED_MD, title="Recover")

    assert _doc_count(factory, sid) == 0
    assert _chunk_count(factory, sid) == 0

    # Undo monkeypatch, retry
    monkeypatch.undo()

    result = ingest_document_tx(factory, source_id=sid, text=PAGED_MD, title="Recover")
    assert result.chunk_count > 0
    assert not result.already_existed
    assert _doc_count(factory, sid) == 1
    assert _chunk_count(factory, sid) > 0
    assert _outbox_count(factory, sid) == 1

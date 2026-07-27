"""Task 3.2: ChunkStore.replace_for_source_in — transactional chunk write."""

from __future__ import annotations

import pytest
import uuid as _uuid
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.models import DocumentChunk


@pytest.fixture()
def factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture()
def store(factory):
    return ChunkStore(factory)


def _chunk_dict(text: str, ord_val: int = 0) -> dict:
    return {"text": text, "ord_val": ord_val, "source_ord": ord_val}


def _row_count(session: Session, source_id: str) -> int:
    return (
        session.query(func.count(DocumentChunk.id))
        .filter(DocumentChunk.source_id == source_id)
        .scalar()
        or 0
    )


def test_replace_for_source_in_rollback_undoes_writes(factory, store) -> None:
    """Session.rollback() after replace_for_source_in → rows NOT visible."""
    sid = "src-rollback"
    chunks = [
        {"text": "line one", "heading_path": "", "page_no": None,
         "paragraph_idx": 0, "offset_start": 0, "offset_end": 8},
        {"text": "line two", "heading_path": "", "page_no": None,
         "paragraph_idx": 1, "offset_start": 9, "offset_end": 17},
    ]

    with factory() as session:
        store.replace_for_source_in(session, sid, chunks)

        # Visible within the uncommitted session
        assert _row_count(session, sid) == 2

        session.rollback()

    # After rollback, no rows should be in the DB
    with factory() as session:
        assert _row_count(session, sid) == 0


def test_replace_for_source_in_commit_persists(factory, store) -> None:
    """Session.commit() after replace_for_source_in → rows visible after."""
    sid = "src-commit"
    chunks = [
        {"text": "persist me", "heading_path": "", "page_no": None,
         "paragraph_idx": 0, "offset_start": 0, "offset_end": 9},
    ]

    with factory() as session:
        store.replace_for_source_in(session, sid, chunks)

        # Bump generation after commit (caller's responsibility)
        session.commit()
        store.bump_generation()

    with factory() as session:
        assert _row_count(session, sid) == 1


def test_replace_for_source_in_idempotent_re_run(factory, store) -> None:
    """Calling replace_for_source_in twice → second run replaces cleanly."""
    sid = "src-idem"
    chunks = [
        {"text": "version 1", "heading_path": "", "page_no": None,
         "paragraph_idx": 0, "offset_start": 0, "offset_end": 9},
    ]
    chunks_v2 = [
        {"text": "version 2", "heading_path": "", "page_no": None,
         "paragraph_idx": 0, "offset_start": 0, "offset_end": 9},
    ]

    with factory() as session:
        store.replace_for_source_in(session, sid, chunks)
        session.commit()
        store.bump_generation()

    with factory() as session:
        store.replace_for_source_in(session, sid, chunks_v2)
        session.commit()
        store.bump_generation()

    # Only the second set should remain
    rows = store.get_by_source(sid)
    assert len(rows) == 1
    assert rows[0].text == "version 2"

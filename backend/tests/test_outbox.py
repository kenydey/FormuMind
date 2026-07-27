"""Tests for the task_outbox idempotency foundation (Task 1.1).

The ``task_outbox`` table is the durable outbox for async task dispatch:
``enqueue`` must be idempotent on ``(operation, idempotency_key)`` so that
retried submissions never duplicate work, and ``select_pending`` feeds the
dispatcher in FIFO (created_at) order. Rows survive rollback cleanly — a
failed enqueue must not leak a partially-written row.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.database import make_engine, make_session_factory
from app.db.models import TaskOutbox
from app.db.outbox_store import enqueue, select_pending
from tests.alembic_helpers import run_upgrade


@pytest.fixture()
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fresh SQLite DB migrated to head, exposed as an ORM session."""
    db_url = f"sqlite:///{tmp_path}/outbox.db"
    run_upgrade(db_url, monkeypatch)
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    with factory() as s:
        yield s
    engine.dispose()


def _row_count(session: Session) -> int:
    return len(session.execute(select(TaskOutbox)).scalars().all())


def test_task_outbox_table_created_by_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh database upgraded to head contains ``task_outbox``."""
    from sqlalchemy import create_engine, inspect

    db_url = f"sqlite:///{tmp_path}/fresh.db"
    run_upgrade(db_url, monkeypatch)
    engine = create_engine(db_url)
    try:
        assert "task_outbox" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_enqueue_duplicate_returns_existing(session: Session) -> None:
    """Same (operation, idempotency_key) enqueued twice → same row, no dup."""
    first_id, first_status = enqueue(session, "ingest", "key-1", {"a": 1})
    second_id, second_status = enqueue(session, "ingest", "key-1", {"a": 1})

    assert first_status == "PENDING"
    assert second_id == first_id
    assert second_status == first_status
    assert _row_count(session) == 1


def test_enqueue_rollback_no_leak(session: Session) -> None:
    """A rollback after a partial flush leaves no row behind."""
    txn = session.begin()
    try:
        session.add(
            TaskOutbox(
                id="bad-row",
                operation="ingest",
                idempotency_key="boom",
            )
        )
        session.flush()
    finally:
        session.rollback()
    assert _row_count(session) == 0


def test_select_pending_order_and_limit(session: Session) -> None:
    """select_pending returns PENDING rows oldest-first, honoring ``limit``."""
    base = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)
    for i in range(3):
        row = TaskOutbox(
            id=f"row-{i}",
            operation="ingest",
            idempotency_key=f"key-{i}",
            payload={"i": i},
            created_at=base + timedelta(seconds=i),
            updated_at=base + timedelta(seconds=i),
        )
        session.add(row)
    session.commit()

    rows = select_pending(session, limit=100)
    assert [r.id for r in rows] == ["row-0", "row-1", "row-2"]

    limited = select_pending(session, limit=2)
    assert [r.id for r in limited] == ["row-0", "row-1"]


def test_unique_constraint_enforced(session: Session) -> None:
    """The DB-level unique constraint rejects duplicate keys via raw ORM."""
    session.add(
        TaskOutbox(
            id="one", operation="ingest", idempotency_key="dup-key", payload={}
        )
    )
    session.commit()

    session.add(
        TaskOutbox(
            id="two", operation="ingest", idempotency_key="dup-key", payload={}
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_enqueue_caller_rollback_then_reenqueue(session: Session) -> None:
    """Caller rollback after enqueue → re-enqueue with the same key creates a fresh row."""
    fid, _ = enqueue(session, "ingest", "key-rb", {"x": 1})
    session.rollback()
    assert _row_count(session) == 0  # rollback wiped the flushed row

    # Re-enqueue with the same idempotency key should create a NEW row
    # (not return the old id, which is gone after rollback).
    sid, _ = enqueue(session, "ingest", "key-rb", {"x": 1})
    assert sid != fid, "re-enqueue after rollback must produce a fresh row"
    assert _row_count(session) == 1

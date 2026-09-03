"""Tests for the task_outbox idempotency foundation (Task 1.1) and
stall recovery (Task 1.3).

The ``task_outbox`` table is the durable outbox for async task dispatch:
``enqueue`` must be idempotent on ``(operation, idempotency_key)`` so that
retried submissions never duplicate work, and ``select_pending`` feeds the
dispatcher in FIFO (created_at) order. Rows survive rollback cleanly — a
failed enqueue must not leak a partially-written row.

Task 1.3 adds ``recover_stalled`` which scans for PENDING/CLAIMED rows
older than the cutoff and re-dispatches them via Celery ``.delay()``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.database import make_engine, make_session_factory
from app.db.dispatcher import recover_stalled
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


@pytest.fixture()
def _stub_dispatch(monkeypatch: pytest.MonkeyPatch):
    """Replace app.db.dispatcher._dispatch with a mock for stall-recovery tests.

    autouse=False — only the Task 1.3 tests request this fixture so other
    tests' Celery imports are never affected.
    """
    mock = MagicMock()
    monkeypatch.setattr("app.db.dispatcher._dispatch", mock)
    return mock


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
    """Caller rollback after enqueue → re-enqueue with same key = fresh row."""
    fid, _ = enqueue(session, "ingest", "key-rb", {"x": 1})
    session.rollback()
    assert _row_count(session) == 0  # rollback wiped the flushed row

    # Re-enqueue with the same idempotency key should create a NEW row
    # (not return the old id, which is gone after rollback).
    sid, _ = enqueue(session, "ingest", "key-rb", {"x": 1})
    assert sid != fid, "re-enqueue after rollback must produce a fresh row"
    assert _row_count(session) == 1


# ── Task 1.3: startup stall recovery ────────────────────────────────────────


def test_recover_stalled_resubmits_pending(
    session: Session, _stub_dispatch: MagicMock
) -> None:
    """Stalled PENDING row (1 h old) → re-dispatched, status stays PENDING."""
    import uuid

    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    row = TaskOutbox(
        id=str(uuid.uuid4()),
        operation="research_recommend",
        idempotency_key="stall-key-1",
        payload={"topic": "test", "requirement": {}, "sources": [], "query": "test"},
        status="PENDING",
        created_at=old,
        updated_at=old,
    )
    session.add(row)
    session.commit()

    count = recover_stalled(session, cutoff_minutes=30)
    session.commit()

    assert count == 1
    _stub_dispatch.assert_called_once_with("research_recommend", row.payload)

    # Verify row was updated in DB
    reloaded = session.get(TaskOutbox, row.id)
    assert reloaded is not None
    assert reloaded.status == "PENDING"
    assert reloaded.attempt_count == 1
    assert reloaded.claimed_by is None
    assert reloaded.claimed_at is None


def test_recover_stalled_skips_recent(
    session: Session, _stub_dispatch: MagicMock
) -> None:
    """PENDING row only 5 min old → not re-dispatched."""
    import uuid

    recent = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
    row = TaskOutbox(
        id=str(uuid.uuid4()),
        operation="research_recommend",
        idempotency_key="stall-key-2",
        payload={"topic": "test"},
        status="PENDING",
        created_at=recent,
        updated_at=recent,
    )
    session.add(row)
    session.commit()

    count = recover_stalled(session, cutoff_minutes=30)
    session.commit()

    assert count == 0
    _stub_dispatch.assert_not_called()

    # Row still PENDING, attempt_count unchanged
    reloaded = session.get(TaskOutbox, row.id)
    assert reloaded is not None
    assert reloaded.status == "PENDING"
    assert reloaded.attempt_count == 0


def test_recover_stalled_skips_confirmed(
    session: Session, _stub_dispatch: MagicMock
) -> None:
    """CONFIRMED row (1 h old) → not re-dispatched."""
    import uuid

    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    row = TaskOutbox(
        id=str(uuid.uuid4()),
        operation="research_recommend",
        idempotency_key="stall-key-3",
        payload={"topic": "test"},
        status="CONFIRMED",
        created_at=old,
        updated_at=old,
    )
    session.add(row)
    session.commit()

    count = recover_stalled(session, cutoff_minutes=30)
    session.commit()

    assert count == 0
    _stub_dispatch.assert_not_called()


def test_recover_stalled_resubmits_claimed(
    session: Session, _stub_dispatch: MagicMock
) -> None:
    """Stalled CLAIMED row (1 h old) → re-dispatched, claimed_* cleared."""
    import uuid

    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    row = TaskOutbox(
        id=str(uuid.uuid4()),
        operation="research_deep",
        idempotency_key="stall-key-4",
        payload={"query": "test"},
        status="CLAIMED",
        claimed_by="worker-1",
        claimed_at=old,
        created_at=old,
        updated_at=old,
    )
    session.add(row)
    session.commit()

    count = recover_stalled(session, cutoff_minutes=30)
    session.commit()

    assert count == 1
    _stub_dispatch.assert_called_once_with("research_deep", row.payload)

    reloaded = session.get(TaskOutbox, row.id)
    assert reloaded is not None
    assert reloaded.status == "PENDING"
    assert reloaded.attempt_count == 1
    assert reloaded.claimed_by is None
    assert reloaded.claimed_at is None


def test_recover_stalled_skips_dispatch_error(
    session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When _dispatch raises, the failing row is skipped (left as-is)."""
    import uuid

    import app.db.dispatcher as _dispatch_module

    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    row_a = TaskOutbox(
        id=str(uuid.uuid4()),
        operation="research_recommend",
        idempotency_key="stall-key-err",
        payload={"_test": "should-fail"},
        status="PENDING",
        created_at=old,
        updated_at=old,
    )
    row_b = TaskOutbox(
        id=str(uuid.uuid4()),
        operation="research_recommend",
        idempotency_key="stall-key-ok",
        payload={"_test": "ok"},
        status="PENDING",
        created_at=old + timedelta(seconds=1),
        updated_at=old,
    )
    session.add_all([row_a, row_b])
    session.commit()

    # Make _dispatch fail only for the first row (keyed by idempotency_key).
    def _failing(operation: str, payload: dict) -> None:
        if payload.get("_test") == "should-fail":
            raise RuntimeError("simulated dispatch failure")

    monkeypatch.setattr(_dispatch_module, "_dispatch", _failing)

    count = recover_stalled(session, cutoff_minutes=30)
    session.commit()

    # Only row_b should have been re-dispatched.
    assert count == 1

    # row_a: status still PENDING, attempt_count unchanged (skipped).
    a = session.get(TaskOutbox, row_a.id)
    assert a is not None
    assert a.status == "PENDING"
    assert a.attempt_count == 0

    # row_b: re-dispatched.
    b = session.get(TaskOutbox, row_b.id)
    assert b is not None
    assert b.status == "PENDING"
    assert b.attempt_count == 1

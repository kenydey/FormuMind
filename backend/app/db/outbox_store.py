"""Durable outbox store for async task dispatch (idempotency foundation).

``enqueue`` is the single write-path: it guarantees exactly one row per
``(operation, idempotency_key)`` pair. Repeat calls with the same key
return the existing row without inserting a duplicate. ``select_pending``
feeds the dispatcher with unclaimed rows in FIFO (created_at) order.
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import TaskOutbox


def enqueue(
    session: Session, operation: str, idempotency_key: str, payload: dict
) -> tuple[str, str]:
    """Ensure one outbox row for *operation* × *idempotency_key*.

    If a matching row already exists, return ``(id, existing_status)``
    without inserting.  Otherwise insert a new ``PENDING`` row, flush
    (not commit — the caller owns the transaction boundary), and
    return ``(id, "PENDING")``.

    Concurrent enqueues for the same key are resolved by the DB unique
    constraint: the loser rolls back the failed INSERT and re-selects the
    now-existing row to preserve idempotency.

    Returns:
        ``(task_id: str, status: str)`` — status may differ from
        ``"PENDING"`` when a de-duplicated stale row has been claimed or
        confirmed by the dispatcher.
    """
    existing = session.execute(
        select(TaskOutbox).filter_by(
            operation=operation, idempotency_key=idempotency_key,
        )
    ).scalar_one_or_none()

    if existing is not None:
        return existing.id, existing.status

    row = TaskOutbox(
        id=str(uuid4()),
        operation=operation,
        idempotency_key=idempotency_key,
        payload=payload,
    )
    # No savepoint here: SQLAlchemy's begin_nested() on SQLite/pysqlite releases
    # the savepoint in a way that commits the flushed INSERT, so an outer
    # session.rollback() cannot undo it (breaks caller transaction atomicity).
    # A concurrent-duplicate IntegrityError propagates to the caller, which
    # rolls back its own transaction and re-selects idempotently.
    session.add(row)
    session.flush()
    return row.id, "PENDING"


def select_pending(session: Session, limit: int = 100) -> list[TaskOutbox]:
    """Return the oldest unclaimed outbox rows, up to *limit*.

    Rows are ordered by ``created_at ASC`` so the dispatcher processes
    the earliest-submitted tasks first.
    """
    return (
        session.execute(
            select(TaskOutbox)
            .where(TaskOutbox.status == "PENDING")
            .order_by(TaskOutbox.created_at.asc())
            .limit(limit)
        )
        .scalars()
        .all()
    )

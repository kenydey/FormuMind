"""Durable outbox store for async task dispatch (idempotency foundation).

``enqueue`` is the single write-path: it guarantees exactly one row per
``(operation, idempotency_key)`` pair. Repeat calls with the same key
return the existing row without inserting a duplicate. ``select_pending``
feeds the dispatcher with unclaimed rows in FIFO (created_at) order.
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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
    session.add(row)
    # Use a savepoint so that a concurrent-duplicate IntegrityError only
    # rolls back the INSERT, not other pending changes the caller owns.
    sp = session.begin_nested()
    try:
        session.flush()
    except IntegrityError:
        sp.rollback()
        existing = session.execute(
            select(TaskOutbox).filter_by(
                operation=operation, idempotency_key=idempotency_key,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing.id, existing.status
        raise
    else:
        sp.commit()
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

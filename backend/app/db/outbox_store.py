"""Durable outbox store for async task dispatch (idempotency foundation).

``enqueue`` is the single write-path: it guarantees exactly one row per
``(operation, idempotency_key)`` pair. Repeat calls with the same key
return the existing row without inserting a duplicate. ``select_pending``
feeds the dispatcher with unclaimed rows in FIFO (created_at) order.

Row lifecycle::

    PENDING ──claim──▶ CLAIMED ──dispatch ok──▶ PENDING (attempt_count + 1)
       │                                            │
       └────────── worker finishes (mark_done) ─────┴──▶ DONE
                         recovery gives up ──────────────▶ DEAD

``DONE`` is the completion mark: the job reached a final state (success *or*
failure — the failure itself is recorded in the task snapshot), so crash
recovery must never replay it. ``CONFIRMED`` is reserved for the Datalab
reconciliation phase.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import TaskOutbox

logger = logging.getLogger(__name__)

STATUS_PENDING = "PENDING"
STATUS_CLAIMED = "CLAIMED"
STATUS_DONE = "DONE"
STATUS_DEAD = "DEAD"

# Statuses a finished job may be re-armed from when the same payload is
# submitted again (a new submission is a new unit of work).
_REARMABLE = (STATUS_DONE, STATUS_DEAD)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def canonicalize(obj: Any) -> Any:
    """Recursively sort lists so element order does not affect the hash.

    Two submissions that differ only in the order of a source list are the
    same job, and must not produce two outbox rows.
    """
    if isinstance(obj, dict):
        return {k: canonicalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        items = [canonicalize(i) for i in obj]
        try:
            return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))
        except TypeError:
            return items
    return obj


def idempotency_key(operation: str, payload: dict) -> str:
    """Content-addressed key: sha256 of the operation plus canonical payload.

    Deterministic from ``(operation, payload)`` alone, so the worker can
    recompute it from the payload it received to find its own outbox row.
    """
    data = {"op": operation, "payload": canonicalize(payload)}
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def enqueue(
    session: Session,
    operation: str,
    idempotency_key: str,
    payload: dict,
    *,
    status: str = STATUS_PENDING,
) -> tuple[str, str]:
    """Ensure one outbox row for *operation* × *idempotency_key*.

    If a matching row already exists, return ``(id, existing_status)``
    without inserting.  Otherwise insert a new row (``PENDING`` unless the
    caller records an already-completed event via *status*), flush
    (not commit — the caller owns the transaction boundary), and
    return ``(id, status)``.

    A finished (``DONE`` / ``DEAD``) row that is submitted again with
    ``status=PENDING`` is *re-armed*: the new submission is fresh work, so it
    must be crash-recoverable again and its stall clock restarts.

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
        if status == STATUS_PENDING and existing.status in _REARMABLE:
            now = _utcnow()
            existing.status = STATUS_PENDING
            existing.attempt_count = 0
            existing.claimed_by = None
            existing.claimed_at = None
            existing.payload = payload
            existing.created_at = now
            existing.updated_at = now
            session.flush()
            return existing.id, STATUS_PENDING
        return existing.id, existing.status

    row = TaskOutbox(
        id=str(uuid4()),
        operation=operation,
        idempotency_key=idempotency_key,
        payload=payload,
        status=status,
    )
    # No savepoint here: SQLAlchemy's begin_nested() on SQLite/pysqlite releases
    # the savepoint in a way that commits the flushed INSERT, so an outer
    # session.rollback() cannot undo it (breaks caller transaction atomicity).
    # A concurrent-duplicate IntegrityError propagates to the caller, which
    # rolls back its own transaction and re-selects idempotently.
    session.add(row)
    session.flush()
    return row.id, status


def mark_done(session: Session, operation: str, payload: dict) -> bool:
    """Completion mark: flip the row for *operation* × *payload* to ``DONE``.

    Only in-flight rows (``PENDING`` / ``CLAIMED``) change; a row already
    ``DONE`` / ``DEAD`` / ``CONFIRMED`` is left alone. The caller owns the
    commit. Returns True when a row was updated.
    """
    result = session.execute(
        update(TaskOutbox)
        .where(
            TaskOutbox.operation == operation,
            TaskOutbox.idempotency_key == idempotency_key(operation, payload),
            TaskOutbox.status.in_([STATUS_PENDING, STATUS_CLAIMED]),
        )
        .values(
            status=STATUS_DONE,
            claimed_by=None,
            claimed_at=None,
            updated_at=_utcnow(),
        )
    )
    return bool(result.rowcount)


def record_done(operation: str, payload: dict) -> bool:
    """Completion mark in its own transaction. Never raises.

    Called by the worker once a task reached a final state, so crash recovery
    (``dispatcher.recover_stalled``) only replays jobs that did not finish. A
    missing row (the API-side enqueue failed) is not an error.
    """
    try:
        from .database import default_session_factory
        from .session_utils import commit_session

        with commit_session(default_session_factory()) as session:
            return mark_done(session, operation, payload)
    except Exception:
        logger.exception("outbox mark_done failed (non-fatal)")
        return False


def select_pending(session: Session, limit: int = 100) -> list[TaskOutbox]:
    """Return the oldest unclaimed outbox rows, up to *limit*.

    Rows are ordered by ``created_at ASC`` so the dispatcher processes
    the earliest-submitted tasks first.
    """
    return (
        session.execute(
            select(TaskOutbox)
            .where(TaskOutbox.status == STATUS_PENDING)
            .order_by(TaskOutbox.created_at.asc())
            .limit(limit)
        )
        .scalars()
        .all()
    )

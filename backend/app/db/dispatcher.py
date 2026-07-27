"""Startup outbox stall recovery — re-enqueue stalled async jobs (Task 1.3).

Scans ``task_outbox`` for rows that are still PENDING or CLAIMED beyond a
configurable cutoff (default 30 min) and re-dispatches them via the
Celery ``.delay()`` path so no durable outbox row is left behind after a
crash / redeploy while jobs are in-flight.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import TaskOutbox

logger = logging.getLogger(__name__)

# ── operation → Celery task mapping ─────────────────────────────────────────

def _dispatch(operation: str, payload: dict) -> None:
    """Map an outbox *operation* to the matching Celery task ``.delay()``."""
    if operation == "research_recommend":
        from ..worker.tasks import run_recommend_task

        run_recommend_task.delay(payload)
    elif operation == "research_deep":
        from ..worker.tasks import run_deep_research_task

        run_deep_research_task.delay(payload)
    else:
        logger.warning(
            "recover_stalled: unknown operation %s — skipped", operation
        )


# ── public API ──────────────────────────────────────────────────────────────

def recover_stalled(session: Session, cutoff_minutes: int = 30) -> int:
    """Re-enqueue outbox rows that have been stalled for too long.

    Scans ``task_outbox`` for rows with ``status IN ('PENDING', 'CLAIMED')``
    whose ``created_at`` is older than *now − cutoff_minutes*.  For each
    match the payload is re-dispatched via the Celery ``.delay()`` path and
    the row's status is reset to ``'PENDING'`` (attempt_count incremented).

    Args:
        session: An active SQLAlchemy ORM session.
        cutoff_minutes: Age threshold in minutes (default 30).

    Returns:
        Number of rows that were re-enqueued.

    Raises:
        ValueError: If *cutoff_minutes* is less than 1.
    """
    if cutoff_minutes < 1:
        raise ValueError("cutoff_minutes must be >= 1")
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff -= timedelta(minutes=cutoff_minutes)

    stalled = (
        session.execute(
            select(TaskOutbox)
            .where(
                TaskOutbox.status.in_(["PENDING", "CLAIMED"]),
                TaskOutbox.created_at < cutoff,
            )
        )
        .scalars()
        .all()
    )

    count = 0
    for row in stalled:
        try:
            _dispatch(row.operation, row.payload)
        except Exception:
            logger.exception(
                "recover_stalled: dispatch failed for outbox row %s "
                "(operation=%s)",
                row.id,
                row.operation,
            )
            continue

        row.status = "PENDING"
        row.attempt_count = (row.attempt_count or 0) + 1
        row.claimed_by = None
        row.claimed_at = None
        count += 1

    if count:
        logger.info("recover_stalled: re-enqueued %d stalled outbox rows", count)

    return count

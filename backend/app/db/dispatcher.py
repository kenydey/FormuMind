"""Startup outbox stall recovery — re-enqueue stalled async jobs (Task 1.3).

Scans ``task_outbox`` for rows that are still PENDING or CLAIMED beyond a
configurable cutoff (default 30 min) and re-dispatches them via the
Celery ``.delay()`` path so no durable outbox row is left behind after a
crash / redeploy while jobs are in-flight.

Lifespan MUST schedule recovery in a daemon thread (see
``schedule_recover_stalled``): with ``FORMUMIND_CELERY_EAGER=true``, a
synchronous ``.delay()`` would run whole research/inverse tasks on the
startup thread and keep :8000 closed — the middle-column ``Load failed`` /
``/api/projects -> 500`` failure mode.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import TaskOutbox

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
# Cap how many stalled rows one recovery pass will touch (startup budget).
DEFAULT_MAX_ROWS = 20
# Broker publish timeout for non-eager ``.delay()`` (seconds).
DEFAULT_DISPATCH_TIMEOUT_S = 5.0

_worker_id = f"{socket.gethostname()}-{os.getpid()}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _celery_is_eager() -> bool:
    """Read effective eager flag without importing Celery app at module load."""
    try:
        from ..config import get_settings

        return bool(get_settings().celery_eager)
    except Exception:
        logger.exception("recover_stalled: failed to read celery_eager; assuming False")
        return False


# ── operation → Celery task mapping ─────────────────────────────────────────

def _dispatch(operation: str, payload: dict) -> None:
    """Map an outbox *operation* to the matching Celery task ``.delay()``."""
    if operation == "research_recommend":
        from ..worker.tasks import run_recommend_task

        run_recommend_task.delay(payload)
    elif operation == "research_deep":
        from ..worker.tasks import run_deep_research_task

        run_deep_research_task.delay(payload)
    elif operation == "inverse_design":
        from ..worker.tasks import run_inverse_design_task

        run_inverse_design_task.delay(payload)
    elif operation == "ingest_complete":
        logger.info("ingest_complete task acknowledged (payload=%s)", payload)
    else:
        logger.warning(
            "recover_stalled: unknown operation %s — skipped", operation
        )


def _dispatch_with_timeout(
    operation: str, payload: dict, timeout_s: float = DEFAULT_DISPATCH_TIMEOUT_S
) -> None:
    """Publish to the broker with a hard wall-clock limit (non-eager path)."""
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="outbox-delay") as pool:
        fut = pool.submit(_dispatch, operation, payload)
        try:
            fut.result(timeout=timeout_s)
        except FuturesTimeout as exc:
            raise TimeoutError(
                f"Celery publish timed out after {timeout_s:.1f}s for {operation}"
            ) from exc


# ── public API ──────────────────────────────────────────────────────────────

def recover_stalled(
    session: Session,
    cutoff_minutes: int = 30,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    dispatch_timeout_s: float = DEFAULT_DISPATCH_TIMEOUT_S,
) -> int:
    """Re-enqueue outbox rows that have been stalled for too long.

    Scans ``task_outbox`` for rows with ``status IN ('PENDING', 'CLAIMED')``
    whose ``created_at`` is older than *now − cutoff_minutes*.  For each
    match the payload is re-dispatched via the Celery ``.delay()`` path and
    the row's status is reset to ``'PENDING'`` (attempt_count incremented).

    Prefer ``recover_stalled_for_startup`` / ``schedule_recover_stalled`` from
    lifespan: those skip re-dispatch when ``celery_eager`` is True so a local
    eager boot cannot block on full task bodies.

    Args:
        session: An active SQLAlchemy ORM session.
        cutoff_minutes: Age threshold in minutes (default 30).
        max_rows: Maximum rows to process in this pass (startup budget).
        dispatch_timeout_s: Broker publish timeout when not eager.

    Returns:
        Number of rows that were re-enqueued.

    Raises:
        ValueError: If *cutoff_minutes* is less than 1.
    """
    if cutoff_minutes < 1:
        raise ValueError("cutoff_minutes must be >= 1")
    if max_rows < 1:
        raise ValueError("max_rows must be >= 1")
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff -= timedelta(minutes=cutoff_minutes)

    stalled = (
        session.execute(
            select(TaskOutbox)
            .where(
                TaskOutbox.status.in_(["PENDING", "CLAIMED"]),
                TaskOutbox.created_at < cutoff,
            )
            .order_by(TaskOutbox.created_at.asc())
            .limit(max_rows)
        )
        .scalars()
        .all()
    )

    if not stalled:
        return 0

    count = 0
    for row in stalled:
        if (row.attempt_count or 0) >= MAX_ATTEMPTS:
            row.status = "DEAD"
            logger.error(
                "task %s exceeded max attempts (%d), marking DEAD",
                row.id,
                MAX_ATTEMPTS,
            )
            session.commit()
            continue

        row.status = "CLAIMED"
        row.claimed_by = _worker_id
        row.claimed_at = _utcnow()
        session.commit()

        try:
            _dispatch_with_timeout(row.operation, row.payload, dispatch_timeout_s)
        except Exception:
            logger.exception(
                "recover_stalled: dispatch failed for outbox row %s "
                "(operation=%s)",
                row.id,
                row.operation,
            )
            try:
                session.rollback()
            except Exception:
                logger.exception("recover_stalled: rollback failed")
            row = session.get(TaskOutbox, row.id)
            if row is None:
                continue
            # Skipped: reset claim metadata without bumping attempt_count —
            # only successful dispatches are counted toward MAX_ATTEMPTS.
            row.status = "PENDING"
            row.claimed_by = None
            row.claimed_at = None
            session.commit()
            continue

        row.status = "PENDING"
        row.attempt_count = (row.attempt_count or 0) + 1
        row.claimed_by = None
        row.claimed_at = None
        session.commit()
        count += 1

    if count:
        logger.info("recover_stalled: re-enqueued %d stalled outbox rows", count)

    return count


def recover_stalled_for_startup(
    session: Session,
    cutoff_minutes: int = 30,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    dispatch_timeout_s: float = DEFAULT_DISPATCH_TIMEOUT_S,
) -> int:
    """Startup-safe wrapper: never synchronously re-dispatch under celery_eager.

    Local/dev often sets ``FORMUMIND_CELERY_EAGER=true``. Calling ``.delay()``
    then runs recommend/deep-research/inverse-design inline and can stall
    uvicorn before it binds :8000. Leave rows PENDING for an explicit retry
    or a real worker instead.
    """
    if _celery_is_eager():
        # Count how many would have been touched so operators see why nothing ran.
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None)
        cutoff -= timedelta(minutes=cutoff_minutes)
        n = len(
            session.execute(
                select(TaskOutbox.id)
                .where(
                    TaskOutbox.status.in_(["PENDING", "CLAIMED"]),
                    TaskOutbox.created_at < cutoff,
                )
                .limit(max_rows)
            )
            .scalars()
            .all()
        )
        if n:
            logger.warning(
                "recover_stalled: celery_eager=true — leaving %d stalled outbox "
                "row(s) PENDING (sync .delay() would block API startup)",
                n,
            )
        return 0
    return recover_stalled(
        session,
        cutoff_minutes,
        max_rows=max_rows,
        dispatch_timeout_s=dispatch_timeout_s,
    )


def schedule_recover_stalled() -> threading.Thread:
    """Run startup recovery in a daemon thread so lifespan can ``yield`` immediately.

    Failures are logged and never raised to the caller — matching the old
    best-effort lifespan try/except contract. Broker hangs are bounded by
    ``dispatch_timeout_s`` inside ``recover_stalled``.
    """

    def _run() -> None:
        try:
            from ..config import get_settings
            from .database import default_session_factory
            from .sqlite_lock import sqlite_write_lock

            settings = get_settings()
            factory = default_session_factory()
            with sqlite_write_lock(settings.redis_url):
                with factory() as session:
                    recovered = recover_stalled_for_startup(session)
                    if recovered:
                        logger.info(
                            "lifespan: recovered %d stalled outbox row(s)", recovered
                        )
                    session.commit()
        except Exception:
            logger.exception("lifespan: outbox stall recovery failed (non-fatal)")

    thread = threading.Thread(
        target=_run, name="outbox-recover", daemon=True
    )
    thread.start()
    return thread

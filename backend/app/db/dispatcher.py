"""Startup outbox stall recovery — re-enqueue stalled async jobs (Task 1.3).

Scans ``task_outbox`` for rows that are still PENDING or CLAIMED beyond a
configurable cutoff (default 30 min) and re-dispatches them via the
Celery ``.delay()`` path so no durable outbox row is left behind after a
crash / redeploy while jobs are in-flight.

A row is only ever replayed while it is *unfinished*: the worker flips it to
``DONE`` when the task reaches a final state (``outbox_store.record_done``),
so recovery covers crash / redeploy / lost-broker-message cases only. "Stalled"
means *nothing has touched the row* for the cutoff: a running task keeps
``updated_at`` fresh through ``outbox_store.heartbeat``, so a job that simply runs
longer than the cutoff is not replayed underneath itself. A replay runs under the
Celery id the client was given (``task_id``) when it is known. Rows whose operation
has no Celery handler (audit records such as ``ingest_complete``) are never
replayed, and rows untouched for ``MAX_RECOVER_AGE_HOURS`` are expired instead.
``datalab_orphan_cleanup`` rows are consumed by :func:`drain_orphans`.

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
from typing import Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import TaskOutbox

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
# Recovery is for jobs interrupted by a crash / redeploy, not archaeology: a
# row still unfinished after this long is expired (DEAD) instead of replayed,
# so legacy rows written before the completion mark existed cannot trigger a
# replay storm of long-finished jobs.
MAX_RECOVER_AGE_HOURS = 24
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

def _publish(task, payload: dict, task_id: str | None) -> None:
    """Publish *task*; under the client's original Celery id when we have it."""
    if task_id:
        task.apply_async(args=(payload,), task_id=task_id)
    else:
        task.delay(payload)


def _send_recommend(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_recommend_task

    _publish(run_recommend_task, payload, task_id)


def _send_deep_research(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_deep_research_task

    _publish(run_deep_research_task, payload, task_id)


def _send_inverse_design(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_inverse_design_task

    _publish(run_inverse_design_task, payload, task_id)


def _send_doe_cycle(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_doe_cycle_task

    _publish(run_doe_cycle_task, payload, task_id)


# Single source of truth for what recovery may replay. Every operation that
# ``api/*`` enqueues through ``enqueue_outbox`` MUST have an entry here (a
# test pins that); audit-only operations deliberately do not.
_HANDLERS: dict[str, Callable[..., None]] = {
    "research_recommend": _send_recommend,
    "research_deep": _send_deep_research,
    "inverse_design": _send_inverse_design,
    "doe_cycle": _send_doe_cycle,
}

DISPATCHABLE_OPERATIONS: tuple[str, ...] = tuple(_HANDLERS)


def _dispatch(operation: str, payload: dict, task_id: str | None = None) -> None:
    """Map an outbox *operation* to the matching Celery task and publish it.

    With *task_id* the replay runs under the id the client already holds. Raises
    ``ValueError`` for an operation without a handler so the caller never counts a
    no-op as a successful re-enqueue.
    """
    handler = _HANDLERS.get(operation)
    if handler is None:
        raise ValueError(f"no outbox handler for operation {operation!r}")
    if task_id:
        handler(payload, task_id)
    else:
        handler(payload)


def _dispatch_with_timeout(
    operation: str,
    payload: dict,
    timeout_s: float = DEFAULT_DISPATCH_TIMEOUT_S,
    task_id: str | None = None,
) -> None:
    """Publish to the broker with a hard wall-clock limit (non-eager path)."""
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="outbox-delay") as pool:
        fut = (
            pool.submit(_dispatch, operation, payload, task_id)
            if task_id
            else pool.submit(_dispatch, operation, payload)
        )
        try:
            fut.result(timeout=timeout_s)
        except FuturesTimeout as exc:
            raise TimeoutError(
                f"Celery publish timed out after {timeout_s:.1f}s for {operation}"
            ) from exc


def _expire_ancient(session: Session, expiry: datetime) -> int:
    """Mark unfinished dispatchable rows untouched since *expiry* ``DEAD`` (no replay).

    One bulk UPDATE, outside the per-pass ``max_rows`` budget: otherwise the
    oldest (least useful) rows would eat the budget one pass at a time.
    """
    result = session.execute(
        update(TaskOutbox)
        .where(
            TaskOutbox.status.in_(["PENDING", "CLAIMED"]),
            TaskOutbox.operation.in_(DISPATCHABLE_OPERATIONS),
            TaskOutbox.updated_at < expiry,
        )
        .values(status="DEAD")
    )
    n = int(result.rowcount or 0)
    if n:
        session.commit()
        logger.warning(
            "recover_stalled: expired %d unfinished outbox row(s) untouched since %s "
            "(not replayed)",
            n,
            expiry.isoformat(timespec="seconds"),
        )
    return n


# ── public API ──────────────────────────────────────────────────────────────

def recover_stalled(
    session: Session,
    cutoff_minutes: int = 30,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    dispatch_timeout_s: float = DEFAULT_DISPATCH_TIMEOUT_S,
    max_age_hours: float = MAX_RECOVER_AGE_HOURS,
) -> int:
    """Re-enqueue outbox rows that have been stalled for too long.

    Scans ``task_outbox`` for rows with ``status IN ('PENDING', 'CLAIMED')``
    that nothing has touched (``updated_at``: creation, claim, or a running
    task's heartbeat) since *now − cutoff_minutes*.  For each match the payload
    is re-dispatched — under the original Celery id when the row recorded it,
    else via ``.delay()`` — and the row's status is reset to ``'PENDING'``
    (attempt_count incremented).

    Prefer ``recover_stalled_for_startup`` / ``schedule_recover_stalled`` from
    lifespan: those skip re-dispatch when ``celery_eager`` is True so a local
    eager boot cannot block on full task bodies.

    Args:
        session: An active SQLAlchemy ORM session.
        cutoff_minutes: Age threshold in minutes (default 30).
        max_rows: Maximum rows to process in this pass (startup budget).
        dispatch_timeout_s: Broker publish timeout when not eager.
        max_age_hours: Rows older than this are expired (``DEAD``) instead of
            replayed.

    Returns:
        Number of rows that were re-enqueued.

    Raises:
        ValueError: If *cutoff_minutes* is less than 1.
    """
    if cutoff_minutes < 1:
        raise ValueError("cutoff_minutes must be >= 1")
    if max_rows < 1:
        raise ValueError("max_rows must be >= 1")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(minutes=cutoff_minutes)

    _expire_ancient(session, now - timedelta(hours=max_age_hours))

    stalled = (
        session.execute(
            select(TaskOutbox)
            .where(
                TaskOutbox.status.in_(["PENDING", "CLAIMED"]),
                TaskOutbox.operation.in_(DISPATCHABLE_OPERATIONS),
                TaskOutbox.updated_at < cutoff,
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
            if row.task_id:
                _dispatch_with_timeout(
                    row.operation, row.payload, dispatch_timeout_s, task_id=row.task_id
                )
            else:
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


# ── orphaned Datalab samples ────────────────────────────────────────────────

ORPHAN_OPERATION = "datalab_orphan_cleanup"


def _orphan_rows(session: Session, *, status: str | None, limit: int) -> list[TaskOutbox]:
    stmt = select(TaskOutbox).where(TaskOutbox.operation == ORPHAN_OPERATION)
    if status:
        stmt = stmt.where(TaskOutbox.status == status)
    return list(session.execute(stmt.order_by(TaskOutbox.created_at.asc()).limit(limit)).scalars().all())


def list_orphans(session: Session, *, status: str | None = None, limit: int = 100) -> dict:
    """Pending / finished orphan-cleanup rows plus per-status counts (for the ops view)."""
    from sqlalchemy import func

    counts = {
        str(st): int(n)
        for st, n in session.execute(
            select(TaskOutbox.status, func.count())
            .where(TaskOutbox.operation == ORPHAN_OPERATION)
            .group_by(TaskOutbox.status)
        ).all()
    }
    rows = _orphan_rows(session, status=status, limit=max(1, min(int(limit), 500)))
    return {
        "counts": counts,
        "items": [
            {
                "id": r.id,
                "item_id": (r.payload or {}).get("item_id"),
                "kind": (r.payload or {}).get("kind"),
                "status": r.status,
                "attempts": r.attempt_count or 0,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ],
    }


def drain_orphans(
    session: Session,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_attempts: int = MAX_ATTEMPTS,
    deleter: Callable[[str], None] | None = None,
) -> dict:
    """Delete the Datalab samples that a failed saga rollback left behind.

    Creating a campaign / training record is a saga: samples are created one by one and
    compensated (deleted) if a later step fails. When the compensation itself fails —
    Datalab flaky at exactly that moment — the sample id is recorded as a
    ``datalab_orphan_cleanup`` outbox row. Nothing used to read those rows, so the ELN
    kept samples no experiment pointed at, forever.

    Each pending row is retried: success → ``DONE``; failure → attempt counted, ``DEAD``
    after *max_attempts* (the log line then names the sample for manual deletion). When
    Datalab is not configured or unreachable nothing is attempted and no attempt is
    burned — an outage must not use up the retries.

    *deleter* is the seam for tests; by default it calls Datalab's ``/delete-sample/``.
    Returns a summary dict (``examined`` / ``cleaned`` / ``failed`` / ``dead`` / ``skipped``).
    """
    summary: dict = {"examined": 0, "cleaned": 0, "failed": 0, "dead": 0, "skipped": ""}
    rows = _orphan_rows(session, status="PENDING", limit=max_rows)
    if not rows:
        return summary

    if deleter is None:
        from ..config import get_settings
        from .datalab_client import check_datalab_reachable, delete_sample_sync

        api_url = get_settings().datalab_api_url
        if not api_url:
            summary["skipped"] = "FORMUMIND_DATALAB_API_URL is not configured"
            return summary
        reachable, reason = check_datalab_reachable(api_url)
        if not reachable:
            summary["skipped"] = f"datalab unreachable: {reason}"
            return summary

        def deleter(item_id: str, _url: str = api_url) -> None:
            delete_sample_sync(_url, item_id)

    for row in rows:
        summary["examined"] += 1
        item_id = str((row.payload or {}).get("item_id") or "").strip()
        if not item_id:
            row.status = "DEAD"
            summary["dead"] += 1
            session.commit()
            continue
        try:
            deleter(item_id)
        except Exception as exc:
            row.attempt_count = (row.attempt_count or 0) + 1
            summary["failed"] += 1
            if row.attempt_count >= max_attempts:
                row.status = "DEAD"
                summary["dead"] += 1
                logger.error(
                    "datalab orphan %s could not be deleted after %d attempts (%s) — "
                    "delete it in Datalab by hand",
                    item_id,
                    row.attempt_count,
                    exc,
                )
            else:
                logger.warning("datalab orphan %s not deleted yet: %s", item_id, exc)
            session.commit()
            continue
        row.status = "DONE"
        summary["cleaned"] += 1
        session.commit()
    if summary["cleaned"]:
        logger.info("drain_orphans: deleted %d orphaned Datalab sample(s)", summary["cleaned"])
    return summary


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
                    TaskOutbox.operation.in_(DISPATCHABLE_OPERATIONS),
                    TaskOutbox.updated_at < cutoff,
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
            # Not a Celery replay, so it also runs under celery_eager. Outside the write
            # lock on purpose: it talks to Datalab (up to 10 s per sample) and must not
            # keep other writers waiting.
            with factory() as session:
                drain_orphans(session)
        except Exception:
            logger.exception("lifespan: outbox stall recovery failed (non-fatal)")

    thread = threading.Thread(
        target=_run, name="outbox-recover", daemon=True
    )
    thread.start()
    return thread

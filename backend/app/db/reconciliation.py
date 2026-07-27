"""Task 1.6: Datalab reconciliation script — local CONFIRMED rows ↔ Datalab ELN.

Checks whether every CONFIRMED ``task_outbox`` row that carries an
``experiment_id`` in its payload has a corresponding record in the Datalab
Headless ELN (``GET /get-item-data/formumind_exp_{experiment_id}`` → 200).

This is a lightweight detection-only script; it does not auto-repair.
PENDING / CLAIMED rows are ignored — they represent in-flight work.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import TaskOutbox

logger = logging.getLogger(__name__)


def _datalab_item_id(experiment_id: str) -> str:
    """Build the Datalab ELN item ID from a local experiment_id."""
    return f"formumind_exp_{experiment_id}"


def reconcile(
    session: Session,
    datalab_base_url: str,
    *,
    campaign_id: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Reconcile CONFIRMED outbox rows against Datalab ELN records.

    For every CONFIRMED row in ``task_outbox`` whose payload contains an
    ``experiment_id``, the script calls ``GET /get-item-data/`` on the
    Datalab API.  Rows without an ``experiment_id`` in their payload are
    counted as ``skipped``.

    Args:
        session: Active SQLAlchemy ORM session.
        datalab_base_url: Datalab ELN base URL (e.g. ``http://localhost:5001``).
        campaign_id: Optional filter — only reconcile rows whose payload
            matches this campaign_id.
        timeout: HTTP request timeout in seconds (default 30).

    Returns:
        ``{matched, missing, extra, errors, skipped}`` — counts keyed by outcome:
        * **matched** — Datalab returned 200.
        * **missing** — Datalab returned 404.
        * **extra** — reserved (always 0 for now).
        * **errors** — HTTP failure (non-200/404 or connection error).
        * **skipped** — row payload has no ``experiment_id``.
    """
    base_url = datalab_base_url.rstrip("/")
    query = select(TaskOutbox).where(TaskOutbox.status == "CONFIRMED")

    rows: list[TaskOutbox] = list(session.execute(query).scalars().all())

    # Optional campaign_id filter on the payload dict.
    if campaign_id:
        rows = [
            r
            for r in rows
            if isinstance(r.payload, dict)
            and r.payload.get("campaign_id") == campaign_id
        ]

    matched = 0
    missing = 0
    errors = 0
    skipped = 0

    with httpx.Client(base_url=base_url, timeout=timeout) as client:
        for row in rows:
            payload: dict = row.payload if isinstance(row.payload, dict) else {}
            experiment_id = payload.get("experiment_id")

            if not experiment_id:
                logger.debug(
                    "Skipping outbox row %s: payload has no experiment_id",
                    row.id,
                )
                skipped += 1
                continue

            item_id = _datalab_item_id(str(experiment_id))
            try:
                resp = client.get(f"/get-item-data/{item_id}")
                if resp.status_code == 200:
                    matched += 1
                elif resp.status_code == 404:
                    missing += 1
                    logger.info(
                        "Datalab item %s not found (outbox row %s)",
                        item_id,
                        row.id,
                    )
                else:
                    errors += 1
                    logger.warning(
                        "Datalab unexpected status %d for %s (outbox row %s)",
                        resp.status_code,
                        item_id,
                        row.id,
                    )
            except httpx.RequestError as exc:
                errors += 1
                logger.error(
                    "Datalab request failed for %s (outbox row %s): %s",
                    item_id,
                    row.id,
                    exc,
                )

    return {
        "matched": matched,
        "missing": missing,
        "extra": 0,
        "errors": errors,
        "skipped": skipped,
    }

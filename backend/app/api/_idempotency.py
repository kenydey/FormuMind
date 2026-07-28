"""Content-addressed idempotency keys for async job submission.

Async endpoints record a durable outbox row before dispatching to Celery so a
retried submission is recognised as the same job rather than queued twice. The
key is a hash of the operation plus the canonicalised payload.

Extracted from ``api/research.py`` when the third endpoint needed it.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from loguru import logger

from ..db import outbox_store
from ..db.database import default_session_factory


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
    data = {"op": operation, "payload": canonicalize(payload)}
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def enqueue_outbox(operation: str, payload: dict) -> str | None:
    """Record the job durably. Returns the outbox id, or None on failure.

    Non-fatal by design: losing the audit row must not stop the job from
    running.
    """
    try:
        key = idempotency_key(operation, payload)
        factory = default_session_factory()
        with factory() as session:
            task_id, _ = outbox_store.enqueue(session, operation, key, payload)
            session.commit()
        return task_id
    except Exception:
        logger.exception("outbox enqueue failed (non-fatal)")
        return None

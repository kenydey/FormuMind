"""Content-addressed idempotency keys for async job submission.

Async endpoints record a durable outbox row before dispatching to Celery so a
retried submission is recognised as the same job rather than queued twice. The
key is a hash of the operation plus the canonicalised payload.

Extracted from ``api/research.py`` when the third endpoint needed it. The key
function itself lives in ``db.outbox_store`` (the worker recomputes it to mark
its own row ``DONE``); it is re-exported here for the existing call sites.
"""
from __future__ import annotations

from loguru import logger

from ..db import outbox_store
from ..db.database import default_session_factory
from ..db.outbox_store import canonicalize, idempotency_key  # noqa: F401  (re-export)
from ..db.session_utils import commit_session


def enqueue_outbox(operation: str, payload: dict) -> str | None:
    """Record the job durably. Returns the outbox id, or None on failure.

    Non-fatal by design: losing the audit row must not stop the job from
    running.
    """
    try:
        key = idempotency_key(operation, payload)
        factory = default_session_factory()
        with commit_session(factory) as session:
            task_id, _ = outbox_store.enqueue(session, operation, key, payload)
        return task_id
    except Exception:
        logger.exception("outbox enqueue failed (non-fatal)")
        return None


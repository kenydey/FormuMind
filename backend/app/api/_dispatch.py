"""Celery submission that survives an unreachable broker.

Every async endpoint ends the same way: hand a payload to ``.delay()`` and
return 202 with a stream URL. When Redis is down that call does not fail
quickly or cleanly — Celery retries the *result backend* connection for ~19s
and then raises

    RuntimeError: Retry limit exceeded while trying to reconnect to the Celery
    result store backend. The Celery application must be restarted.

which reaches the client as a bare ``500 Internal Server Error`` with a
plain-text body. The frontend parses error bodies as JSON, so an unparseable
body degrades to ``"<path> -> <status>"`` — a message that names no cause and
suggests no action. Behind nginx the same outage surfaces as a 502 instead,
which reads like a different problem but is not.

Two changes fix that:

* **Preflight the broker.** A TCP connect with a one-second timeout tells us
  what 19 seconds of Celery retries would, without the wait or the exception
  that leaves the app's result consumer needing a restart.
* **Answer with 503 and a reason.** The durable outbox row is written before
  dispatch, so a failed submission is recorded work, not lost work —
  ``dispatcher.recover_stalled`` re-enqueues it once the broker is back. 503
  says exactly that: temporarily unavailable, come back.
"""
from __future__ import annotations

import socket
from urllib.parse import urlparse

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from loguru import logger

BROKER_PROBE_TIMEOUT_S = 1.0

BROKER_DOWN_DETAIL = (
    "任务队列（Redis）当前不可达，无法提交后台任务。请确认 redis 与 celery "
    "worker 已启动（docker compose ps），服务恢复后本次提交会自动重新入队。"
)


def _broker_endpoint() -> tuple[str, int] | None:
    """``(host, port)`` for the configured broker, or None if not a TCP URL."""
    from ..config import get_settings

    url = urlparse(get_settings().redis_url)
    if not url.hostname:
        return None
    return url.hostname, url.port or 6379


def broker_reachable() -> bool:
    """Whether a task submitted now would reach a broker.

    Eager mode runs tasks in-process and needs no broker at all, so it is
    always reachable. A unix-socket or otherwise unparseable URL is reported
    reachable too: the probe cannot speak for it, and refusing to submit on
    the strength of a check that did not run would be worse than letting
    Celery try.
    """
    from ..config import get_settings

    if get_settings().celery_eager:
        return True
    endpoint = _broker_endpoint()
    if endpoint is None:
        return True
    try:
        with socket.create_connection(endpoint, timeout=BROKER_PROBE_TIMEOUT_S):
            return True
    except OSError as exc:
        logger.warning("broker unreachable at {}:{} ({})", *endpoint, exc)
        return False


def submit(task, payload: dict, kind: str, *, outbox_id: str | None = None, owner_id: str | None = None) -> JSONResponse:
    """Dispatch ``task`` with ``payload`` and return the 202 accepted response.

    Raises:
        HTTPException: 503 when the broker is unreachable or dispatch fails.
            The outbox row (if one was written) stays PENDING and is recovered
            by ``dispatcher.recover_stalled``.
    """
    from .tasks import accepted_response

    if not broker_reachable():
        raise HTTPException(status_code=503, detail=BROKER_DOWN_DETAIL)
    try:
        async_result = task.delay(payload)
    except Exception as exc:
        # The probe passed a moment ago, so this is a broker that died mid-
        # submission or a payload Celery could not serialise. Either way the
        # client gets a reason instead of an unparseable 500.
        logger.exception("celery dispatch failed for {}", kind)
        raise HTTPException(status_code=503, detail=f"{BROKER_DOWN_DETAIL}（{exc}）") from exc
    return accepted_response(async_result.id, kind, outbox_id=outbox_id, owner_id=owner_id)

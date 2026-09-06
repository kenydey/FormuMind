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

**Eager mode (2026-09-06 E2E audit P0-1):** with ``FORMUMIND_CELERY_EAGER=true``,
``task.delay()`` runs the *entire* job inline. Wrapping that in a 10s producer
timeout produced false 503s (optimize ~15s, loop ~37s) while the work still
finished later, and the error text wrongly blamed Redis. Eager submissions
therefore start a daemon thread and return 202 immediately — same contract as
``workbench_loop`` / KB ingest.
"""
from __future__ import annotations

import socket
import threading
import uuid
from urllib.parse import urlparse

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from loguru import logger

BROKER_PROBE_TIMEOUT_S = 1.0

BROKER_DOWN_DETAIL = (
    "任务队列（Redis）当前不可达，无法提交后台任务。请确认 redis 与 celery "
    "worker 已启动（docker compose ps），服务恢复后本次提交会自动重新入队。"
)

# Producer publish / first-connection stall — not the same as broker down.
DISPATCH_TIMEOUT_DETAIL = (
    "任务提交超时：Celery producer 在时限内未完成入队。请检查 worker 负载"
    "或重启 API 进程；这不代表消息队列一定不可达。"
)

# Non-timeout failure after a green probe — may be serialisation or a flaky
# broker; do not claim Redis is definitively down (ELN/lab errors use their own copy).
DISPATCH_FAILURE_DETAIL = (
    "任务提交失败：Celery 在入队阶段异常。请查看 API 日志确认原因；"
    "若为台账/ELN 相关错误，请检查 Datalab 而非 Redis。"
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


def submit(
    task,
    payload: dict,
    kind: str,
    *,
    outbox_id: str | None = None,
    owner_id: str | None = None,
) -> JSONResponse:
    """Dispatch ``task`` with ``payload`` and return the 202 accepted response.

    Raises:
        HTTPException: 503 when the broker is unreachable or (non-eager)
            producer publish times out / fails. The outbox row (if one was
            written) stays PENDING and is recovered by
            ``dispatcher.recover_stalled``.
    """
    from ..config import get_settings
    from .tasks import accepted_response

    if not broker_reachable():
        raise HTTPException(status_code=503, detail=BROKER_DOWN_DETAIL)

    settings = get_settings()
    if settings.celery_eager:
        task_id = _submit_eager_background(task, payload, kind)
        return accepted_response(task_id, kind, outbox_id=outbox_id, owner_id=owner_id)

    try:
        async_result = _delay_with_timeout(task, payload)
    except _DispatchTimeout as exc:
        # 2026-09-05: uvicorn 进程内 celery producer 首建曾无限卡(所有 submit
        # 端点挂起→前端 502)。预热(lifespan)已把首建移出请求路径; 此处兜底
        # 超时, 宁可明确 503 也不让请求无限挂。
        # 2026-09-06: 文案与 broker-down 拆开，避免把 producer 超时误报成 Redis 不可达。
        logger.error("celery dispatch timed out for {}: {}", kind, exc)
        raise HTTPException(
            status_code=503, detail=f"{DISPATCH_TIMEOUT_DETAIL}（{exc}）"
        ) from exc
    except Exception as exc:
        # The probe passed a moment ago, so this is a broker that died mid-
        # submission or a payload Celery could not serialise. Do not always
        # claim "Redis is down" — that mislabels ELN/serialisation failures.
        logger.exception("celery dispatch failed for {}", kind)
        raise HTTPException(
            status_code=503, detail=f"{DISPATCH_FAILURE_DETAIL}（{exc}）"
        ) from exc
    return accepted_response(async_result.id, kind, outbox_id=outbox_id, owner_id=owner_id)


def _submit_eager_background(task, payload: dict, kind: str) -> str:
    """Run the Celery task body in a daemon thread; return a client task_id now.

    Under ``task_always_eager``, ``task.delay()`` blocks until the job finishes.
    HTTP handlers must not wait on that — return 202 and let SSE/status poll
    observe progress the same way a real worker would.
    """
    task_id = str(uuid.uuid4())

    def _run() -> None:
        try:
            # apply(..., task_id=...) sets self.request.id so progress/publish
            # paths that key off the Celery request id keep working.
            task.apply(args=(payload,), task_id=task_id)
        except Exception:
            logger.exception("eager background task failed kind={} id={}", kind, task_id)

    threading.Thread(
        target=_run,
        name=f"eager-{kind}-{task_id[:8]}",
        daemon=True,
    ).start()
    return task_id


class _DispatchTimeout(Exception):
    pass


def _delay_with_timeout(task, payload: dict, timeout_s: float = 10.0):
    """task.delay in a worker thread with a hard cap (non-eager only).

    Celery's producer pool can block for minutes on first use inside a
    long-lived uvicorn process; the request must never hang on it.
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        fut = pool.submit(task.delay, payload)
        try:
            return fut.result(timeout=timeout_s)
        except FuturesTimeout:
            raise _DispatchTimeout("task.delay > {}s".format(timeout_s)) from None
    finally:
        pool.shutdown(wait=False)  # 卡死线程不等待(超时兜底必须立即返回)

"""Task status snapshot + SSE progress stream (CQRS query side)."""
from __future__ import annotations

import logging
from ..services.errors import log_handled_exception
import asyncio
import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..config import get_settings
from ..domain.schemas import AsyncTaskAccepted, TaskState, TaskStatus
from ..worker.task_progress import (
    TaskProgressEvent,
    TaskProgressStatus,
    channel_name,
    get_task_meta,
    task_exists,
)
from ..worker.tasks import load_persisted_task, task_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["tasks"])

_TERMINAL = (TaskProgressStatus.COMPLETED, TaskProgressStatus.FAILED, TaskProgressStatus.CANCELLED)

# Cap concurrent SSE progress streams to prevent unbounded resource usage.
_SSE_SEMAPHORE = asyncio.Semaphore(50)


def accepted_response(task_id: str, kind: str, outbox_id: str | None = None, owner_id: str | None = None) -> JSONResponse:
    task_manager.register_celery_task(task_id, kind, owner_id=owner_id)
    body = AsyncTaskAccepted(
        task_id=task_id,
        stream_url=f"/api/tasks/{task_id}/stream",
        status_url=f"/api/tasks/{task_id}",
        outbox_id=outbox_id,
    )
    return JSONResponse(status_code=202, content=body.model_dump())


@router.get("/tasks/{task_id}", response_model=TaskStatus)
def get_task(task_id: str, request: Request) -> TaskStatus:
    from ..middleware.api_auth import assert_owner, get_current_owner

    status = task_manager.get(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Unknown task id")
    assert_owner(status.owner_id, get_current_owner(request))
    return status


@router.post("/tasks/{task_id}/cancel", response_model=TaskStatus)
def cancel_task_endpoint(task_id: str, request: Request) -> TaskStatus:
    from ..middleware.api_auth import assert_owner, get_current_owner

    status = task_manager.get(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Unknown task id")
    assert_owner(status.owner_id, get_current_owner(request))
    if status.state in (TaskState.completed, TaskState.failed, TaskState.cancelled):
        return status
    from ..worker.tasks import cancel_task

    cancel_task(task_id)
    # 返回 cancelled 快照（可能仍在 revoke 竞态中，强制返回 cancelled）
    updated = task_manager.get(task_id)
    if updated and updated.state == TaskState.cancelled:
        assert_owner(updated.owner_id, get_current_owner(request))
        return updated
    return TaskStatus(task_id=task_id, kind=status.kind, state=TaskState.cancelled, message="已取消", stream_url=f"/api/tasks/{task_id}/stream", stage="cancelled", owner_id=status.owner_id)


def _terminal_event_from_disk(task_id: str) -> TaskProgressEvent | None:
    persisted = load_persisted_task(task_id)
    if not persisted or persisted.state not in (TaskState.completed, TaskState.failed, TaskState.cancelled):
        return None
    mapping = {
        TaskState.completed: TaskProgressStatus.COMPLETED,
        TaskState.failed: TaskProgressStatus.FAILED,
        TaskState.cancelled: TaskProgressStatus.CANCELLED,
    }
    return TaskProgressEvent(
        status=mapping.get(persisted.state, TaskProgressStatus.FAILED),
        message=persisted.message or ("已取消" if persisted.state == TaskState.cancelled else "done"),
        progress=persisted.progress if persisted.progress else (0.0 if persisted.state == TaskState.cancelled else 1.0),
        data=persisted.result,
        stage=persisted.stage or ("cancelled" if persisted.state == TaskState.cancelled else ""),
    )


def _event_from_meta(meta: dict[str, str]) -> TaskProgressEvent | None:
    raw = meta.get("last_event")
    if not raw:
        return None
    try:
        return TaskProgressEvent.model_validate_json(raw)
    except Exception as exc:
        # Degrade to a RUNNING event instead of going silent so the SSE client
        # still receives a heartbeat when the persisted meta is malformed.
        logger.warning("event parse failed: %s", exc)
        return TaskProgressEvent(
            status=TaskProgressStatus.RUNNING,
            message="operation failed",
            progress=0.0,
        )


def _sse_frame(event: TaskProgressEvent) -> str:
    return f"data: {event.model_dump_json()}\n\n"


async def _poll_until_terminal(
    task_id: str,
    *,
    timeout_s: float | None = None,
) -> AsyncIterator[TaskProgressEvent]:
    """Poll file/meta/disk snapshots when Redis Pub/Sub is unavailable."""
    if timeout_s is None:
        timeout_s = float(get_settings().task_stream_timeout_s)
    deadline = time.monotonic() + timeout_s
    last_payload: str | None = None
    while time.monotonic() < deadline:
        meta = get_task_meta(task_id)
        if meta:
            event = _event_from_meta(meta)
            if event:
                payload = event.model_dump_json()
                if payload != last_payload:
                    last_payload = payload
                    yield event
                    if event.status in _TERMINAL:
                        return

        disk_event = _terminal_event_from_disk(task_id)
        if disk_event:
            yield disk_event
            return

        await asyncio.sleep(0.2)
    # Deadline elapsed with the task still running. Deliberately *not* a FAILED
    # event: the task has not failed, this connection has simply been open long
    # enough. Claiming failure told the UI a healthy multi-hour ingest had died.
    # Closing the stream instead makes the client's EventSource fall back to
    # polling, which reports the real state.
    logger.info(
        "SSE poll deadline reached for task %s after %.0fs — closing stream, "
        "client will fall back to polling",
        task_id, timeout_s,
    )


@router.get("/tasks/{task_id}/stream")
async def stream_task_progress(task_id: str, request: Request) -> StreamingResponse:
    from ..middleware.api_auth import assert_owner, get_current_owner

    persisted = task_manager.get(task_id)
    owner_to_check = persisted.owner_id if persisted else None
    assert_owner(owner_to_check, get_current_owner(request))
    if not task_exists(task_id):
        raise HTTPException(status_code=404, detail="Unknown task id")

    async def event_generator() -> AsyncIterator[str]:
        async with _SSE_SEMAPHORE:
            meta = get_task_meta(task_id)
            if meta:
                event = _event_from_meta(meta)
                if event:
                    yield _sse_frame(event)
                    if event.status in _TERMINAL:
                        return

            disk_event = _terminal_event_from_disk(task_id)
            if disk_event:
                yield _sse_frame(disk_event)
                return

            settings = get_settings()
            try:
                import redis.asyncio as aioredis

                client = aioredis.from_url(settings.redis_url, decode_responses=True)
                await client.ping()
                pubsub = client.pubsub()
                await pubsub.subscribe(channel_name(task_id))
                try:
                    deadline = time.monotonic() + float(
                        get_settings().task_stream_timeout_s
                    )
                    while time.monotonic() < deadline:
                        message = await pubsub.get_message(
                            ignore_subscribe_messages=True, timeout=1.0
                        )
                        if message is None:
                            terminal = _terminal_event_from_disk(task_id)
                            if terminal:
                                yield _sse_frame(terminal)
                                return
                            await asyncio.sleep(0.05)
                            continue
                        if message.get("type") != "message":
                            continue
                        data_raw = message.get("data")
                        if not data_raw:
                            continue
                        try:
                            event = TaskProgressEvent.model_validate_json(data_raw)
                        except Exception:
                            yield f"data: {json.dumps({'status': 'RUNNING', 'message': str(data_raw)}, ensure_ascii=False)}\n\n"
                            continue
                        yield _sse_frame(event)
                        if event.status in _TERMINAL:
                            break
                except asyncio.CancelledError:
                    logger.debug("SSE client disconnected for task %s", task_id)
                    raise
                finally:
                    try:
                        await pubsub.unsubscribe(channel_name(task_id))
                        await pubsub.aclose()
                        await client.aclose()
                    except Exception as exc:
                        log_handled_exception(logger, exc, "handled exception")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("SSE Redis unavailable for %s: %s", task_id, exc)
                async for event in _poll_until_terminal(task_id):
                    yield _sse_frame(event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

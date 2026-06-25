"""Task status snapshot + SSE progress stream (CQRS query side)."""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger

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

router = APIRouter(prefix="/api", tags=["tasks"])

_TERMINAL = (TaskProgressStatus.COMPLETED, TaskProgressStatus.FAILED)


def accepted_response(task_id: str, kind: str) -> JSONResponse:
    task_manager.register_celery_task(task_id, kind)
    body = AsyncTaskAccepted(
        task_id=task_id,
        stream_url=f"/api/tasks/{task_id}/stream",
        status_url=f"/api/tasks/{task_id}",
    )
    return JSONResponse(status_code=202, content=body.model_dump())


@router.get("/tasks/{task_id}", response_model=TaskStatus)
def get_task(task_id: str) -> TaskStatus:
    status = task_manager.get(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Unknown task id")
    return status


def _terminal_event_from_disk(task_id: str) -> TaskProgressEvent | None:
    persisted = load_persisted_task(task_id)
    if not persisted or persisted.state not in (TaskState.completed, TaskState.failed):
        return None
    return TaskProgressEvent(
        status=(
            TaskProgressStatus.COMPLETED
            if persisted.state == TaskState.completed
            else TaskProgressStatus.FAILED
        ),
        message=persisted.message or "done",
        progress=persisted.progress if persisted.progress else 1.0,
        data=persisted.result,
    )


def _event_from_meta(meta: dict[str, str]) -> TaskProgressEvent | None:
    raw = meta.get("last_event")
    if not raw:
        return None
    try:
        return TaskProgressEvent.model_validate_json(raw)
    except Exception:
        return None


def _sse_frame(event: TaskProgressEvent) -> str:
    return f"data: {event.model_dump_json()}\n\n"


async def _poll_until_terminal(
    task_id: str,
    *,
    timeout_s: float = 120.0,
) -> AsyncIterator[TaskProgressEvent]:
    """Poll file/meta/disk snapshots when Redis Pub/Sub is unavailable."""
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


@router.get("/tasks/{task_id}/stream")
async def stream_task_progress(task_id: str) -> StreamingResponse:
    if not task_exists(task_id):
        raise HTTPException(status_code=404, detail="Unknown task id")

    async def event_generator() -> AsyncIterator[str]:
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
                deadline = time.monotonic() + 3600
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
                logger.debug("SSE client disconnected for task {}", task_id)
                raise
            finally:
                try:
                    await pubsub.unsubscribe(channel_name(task_id))
                    await pubsub.aclose()
                    await client.aclose()
                except Exception:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("SSE Redis unavailable for {}: {}", task_id, exc)
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

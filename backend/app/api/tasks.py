"""Task status snapshot + SSE progress stream (CQRS query side)."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger

from ..config import get_settings
from ..domain.schemas import AsyncTaskAccepted, TaskStatus
from ..worker.task_progress import (
    TaskProgressEvent,
    TaskProgressStatus,
    channel_name,
    get_task_meta,
    task_exists,
)
from ..worker.tasks import task_manager

router = APIRouter(prefix="/api", tags=["tasks"])


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


@router.get("/tasks/{task_id}/stream")
async def stream_task_progress(task_id: str) -> StreamingResponse:
    if not task_exists(task_id):
        raise HTTPException(status_code=404, detail="Unknown task id")

    async def event_generator() -> AsyncIterator[str]:
        settings = get_settings()
        meta = get_task_meta(task_id)
        if meta and meta.get("last_event"):
            try:
                event = TaskProgressEvent.model_validate_json(meta["last_event"])
                yield f"data: {event.model_dump_json()}\n\n"
                if event.status in (TaskProgressStatus.COMPLETED, TaskProgressStatus.FAILED):
                    return
            except Exception:
                pass

        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        pubsub = client.pubsub()
        await pubsub.subscribe(channel_name(task_id))
        try:
            import time

            deadline = time.monotonic() + 3600
            while time.monotonic() < deadline:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None:
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
                yield f"data: {event.model_dump_json()}\n\n"
                if event.status in (TaskProgressStatus.COMPLETED, TaskProgressStatus.FAILED):
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

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

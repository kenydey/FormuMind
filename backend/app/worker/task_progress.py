"""Redis Pub/Sub progress bus for Celery async tasks (CQRS query side)."""
from __future__ import annotations

import json
from enum import Enum
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from ..config import get_settings

META_TTL_SECONDS = 86400
RESULT_TTL_SECONDS = 86400


class TaskProgressStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TaskProgressEvent(BaseModel):
    status: TaskProgressStatus
    stage: str = ""
    message: str = ""
    progress: float = 0.0
    data: dict[str, Any] | None = None


class AsyncTaskAccepted(BaseModel):
    task_id: str
    stream_url: str
    status_url: str


def channel_name(task_id: str) -> str:
    return f"task_progress:{task_id}"


def _meta_key(task_id: str) -> str:
    return f"task:meta:{task_id}"


def _result_key(task_id: str) -> str:
    return f"task:result:{task_id}"


def _redis_client():
    import redis

    settings = get_settings()
    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    client.ping()
    return client


def publish_progress(
    task_id: str,
    status: TaskProgressStatus,
    *,
    stage: str = "",
    message: str = "",
    progress: float = 0.0,
    data: dict[str, Any] | None = None,
    kind: str | None = None,
) -> TaskProgressEvent:
    """Publish progress to Redis channel and update meta hash."""
    event = TaskProgressEvent(
        status=status,
        stage=stage,
        message=message,
        progress=progress,
        data=data,
    )
    payload = event.model_dump_json()
    try:
        client = _redis_client()
        client.publish(channel_name(task_id), payload)
        meta: dict[str, str | float] = {
            "status": status.value,
            "stage": stage,
            "message": message,
            "progress": progress,
            "last_event": payload,
        }
        if kind:
            meta["kind"] = kind
        client.hset(_meta_key(task_id), mapping=meta)
        client.expire(_meta_key(task_id), META_TTL_SECONDS)
    except Exception as exc:
        logger.warning("publish_progress failed for {}: {}", task_id, exc)
    return event


def persist_result(
    task_id: str,
    result: dict[str, Any] | None,
    *,
    failed: bool = False,
) -> None:
    try:
        client = _redis_client()
        client.set(
            _result_key(task_id),
            json.dumps(result or {}, ensure_ascii=False),
            ex=RESULT_TTL_SECONDS,
        )
        publish_progress(
            task_id,
            TaskProgressStatus.FAILED if failed else TaskProgressStatus.COMPLETED,
            message="failed" if failed else "done",
            progress=1.0 if not failed else 0.0,
            data=result,
        )
    except Exception as exc:
        logger.warning("persist_result failed for {}: {}", task_id, exc)


def get_task_meta(task_id: str) -> dict[str, str] | None:
    try:
        client = _redis_client()
        meta = client.hgetall(_meta_key(task_id))
        return meta or None
    except Exception:
        return None


def get_task_result(task_id: str) -> dict[str, Any] | None:
    try:
        client = _redis_client()
        raw = client.get(_result_key(task_id))
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        return None


def task_exists(task_id: str) -> bool:
    if get_task_meta(task_id):
        return True
    try:
        from .tasks import load_persisted_task

        return load_persisted_task(task_id) is not None
    except Exception:
        return False


def register_pending(task_id: str, kind: str) -> None:
    publish_progress(
        task_id,
        TaskProgressStatus.PENDING,
        message="queued",
        progress=0.0,
        kind=kind,
    )

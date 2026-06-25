"""Redis Pub/Sub progress bus for Celery async tasks (CQRS query side)."""
from __future__ import annotations

import json
import os
from enum import Enum
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from ..config import get_settings

META_TTL_SECONDS = 86400
RESULT_TTL_SECONDS = 86400

_TASK_DIR = Path(os.environ.get("FORMUMIND_TASK_DIR", "/tmp/formumind_tasks"))
_PROGRESS_DIR = Path(
    os.environ.get("FORMUMIND_TASK_PROGRESS_DIR", str(_TASK_DIR / "progress"))
)


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


def _progress_dir() -> Path:
    _PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    return _PROGRESS_DIR


def _meta_path(task_id: str) -> Path:
    return _progress_dir() / f"{task_id}.meta.json"


def _result_path(task_id: str) -> Path:
    return _progress_dir() / f"{task_id}.result.json"


def _file_write_meta(
    task_id: str,
    event: TaskProgressEvent,
    *,
    kind: str | None = None,
) -> None:
    meta: dict[str, str | float] = {
        "status": event.status.value,
        "stage": event.stage,
        "message": event.message,
        "progress": event.progress,
        "last_event": event.model_dump_json(),
    }
    if kind:
        meta["kind"] = kind
    _meta_path(task_id).write_text(
        json.dumps(meta, ensure_ascii=False),
        encoding="utf-8",
    )


def _file_read_meta(task_id: str) -> dict[str, str] | None:
    path = _meta_path(task_id)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {k: str(v) for k, v in raw.items()}
    except Exception:
        return None


def _file_write_result(task_id: str, result: dict[str, Any] | None) -> None:
    _result_path(task_id).write_text(
        json.dumps(result or {}, ensure_ascii=False),
        encoding="utf-8",
    )


def _file_read_result(task_id: str) -> dict[str, Any] | None:
    path = _result_path(task_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _redis_client():
    import redis

    settings = get_settings()
    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    client.ping()
    return client


def _store_progress(
    task_id: str,
    event: TaskProgressEvent,
    *,
    kind: str | None = None,
) -> None:
    """Publish to Redis; fall back to disk when Redis is unavailable."""
    payload = event.model_dump_json()
    try:
        client = _redis_client()
        client.publish(channel_name(task_id), payload)
        meta: dict[str, str | float] = {
            "status": event.status.value,
            "stage": event.stage,
            "message": event.message,
            "progress": event.progress,
            "last_event": payload,
        }
        if kind:
            meta["kind"] = kind
        client.hset(_meta_key(task_id), mapping=meta)
        client.expire(_meta_key(task_id), META_TTL_SECONDS)
    except Exception as exc:
        logger.warning("progress store failed for {}: {}", task_id, exc)
        _file_write_meta(task_id, event, kind=kind)


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
    _store_progress(task_id, event, kind=kind)
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
    except Exception as exc:
        logger.warning("persist_result redis set failed for {}: {}", task_id, exc)
        _file_write_result(task_id, result)
    publish_progress(
        task_id,
        TaskProgressStatus.FAILED if failed else TaskProgressStatus.COMPLETED,
        message="failed" if failed else "done",
        progress=1.0 if not failed else 0.0,
        data=result,
    )


def get_task_meta(task_id: str) -> dict[str, str] | None:
    try:
        client = _redis_client()
        meta = client.hgetall(_meta_key(task_id))
        if meta:
            return meta
    except Exception:
        pass
    return _file_read_meta(task_id)


def get_task_result(task_id: str) -> dict[str, Any] | None:
    try:
        client = _redis_client()
        raw = client.get(_result_key(task_id))
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return _file_read_result(task_id)


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

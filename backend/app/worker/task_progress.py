"""Redis Pub/Sub progress bus for Celery async tasks (CQRS query side)."""
from __future__ import annotations

import logging
from ..services.errors import degrade_return, log_handled_exception
import json
import os
import time
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..config import get_settings

logger = logging.getLogger(__name__)

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
    CANCELLED = "CANCELLED"


class TaskProgressEvent(BaseModel):
    status: TaskProgressStatus
    stage: str = ""
    message: str = ""
    progress: float = 0.0
    data: dict[str, Any] | None = None
    elapsed_ms: int | None = None


class AsyncTaskAccepted(BaseModel):
    task_id: str
    stream_url: str
    status_url: str


def thinking_step(
    step_id: str,
    title: str,
    *,
    kind: str = "stage",
    detail: str = "",
    status: str = "running",
) -> dict[str, Any]:
    """One row for ``data.thinking`` (Dim-2 thinking timeline)."""
    return {
        "id": step_id,
        "kind": kind if kind in ("stage", "thought", "tool") else "thought",
        "title": title,
        "detail": detail or "",
        "status": status if status in ("pending", "running", "done", "error") else "running",
    }


def attach_thinking(data: dict[str, Any] | None, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge a thinking snapshot into an existing progress ``data`` payload."""
    out = dict(data or {})
    out["thinking"] = list(steps)
    return out


class ThinkingTracker:
    """Accumulate thinking steps and publish full snapshots on each emit.

    Wire format stays compatible: callers still use stage/message/progress;
    UI that understands ``data.thinking`` renders the timeline.
    """

    def __init__(self, task_id: str, *, kind: str | None = None) -> None:
        self.task_id = task_id
        self.kind = kind
        self.steps: list[dict[str, Any]] = []

    def _close_running(self, *, as_status: str = "done") -> None:
        for step in self.steps:
            if step.get("status") == "running":
                step["status"] = as_status

    def emit(
        self,
        stage: str,
        message: str,
        *,
        progress: float = 0.0,
        step_id: str | None = None,
        title: str | None = None,
        kind: str = "stage",
        detail: str = "",
        data: dict[str, Any] | None = None,
        status: TaskProgressStatus = TaskProgressStatus.RUNNING,
    ) -> TaskProgressEvent:
        sid = step_id or stage or f"step-{len(self.steps) + 1}"
        title_s = title or message or stage or sid
        # Upsert: repeated emits for the same stage update in place (optimizer ticks).
        existing = next((s for s in self.steps if s.get("id") == sid), None)
        if existing is not None:
            existing["title"] = title_s
            existing["detail"] = detail or existing.get("detail") or ""
            existing["status"] = "running"
            existing["kind"] = kind if kind in ("stage", "thought", "tool") else existing.get("kind", "stage")
            # Mark other previously-running steps done (keep this one running).
            for step in self.steps:
                if step is not existing and step.get("status") == "running":
                    step["status"] = "done"
        else:
            self._close_running(as_status="done")
            self.steps.append(
                thinking_step(
                    sid,
                    title_s,
                    kind=kind,
                    detail=detail,
                    status="running",
                )
            )
        return publish_progress(
            self.task_id,
            status,
            stage=stage,
            message=message,
            progress=progress,
            data=attach_thinking(data, self.steps),
            kind=self.kind,
        )

    def thought(
        self,
        title: str,
        *,
        stage: str = "",
        message: str = "",
        progress: float = 0.0,
        detail: str = "",
        step_id: str | None = None,
    ) -> TaskProgressEvent:
        return self.emit(
            stage or (self.steps[-1]["id"] if self.steps else "thought"),
            message or title,
            progress=progress,
            step_id=step_id or f"thought-{len(self.steps) + 1}",
            title=title,
            kind="thought",
            detail=detail,
        )

    def finish(self, *, error: bool = False) -> None:
        self._close_running(as_status="error" if error else "done")



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
    except Exception as exc:
        return degrade_return(logger, exc, "operation failed", None)


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
    except Exception as exc:
        return degrade_return(logger, exc, "operation failed", None)


def _redis_client():
    import redis

    settings = get_settings()
    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    # Don't ping on every call — the first operation will fail gracefully
    # if Redis is down, and the result-store / progress-store already have
    # disk fallbacks.
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
        logger.warning("progress store failed for %s: %s", task_id, exc)
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
    # 计算 elapsed_ms（基于 started_at）
    elapsed_ms = None
    try:
        meta = get_task_meta(task_id)
        st = (meta or {}).get("started_at")
        if st is not None:
            elapsed_ms = int((time.time() - float(st)) * 1000)
    except Exception:
        pass
    event = TaskProgressEvent(
        status=status,
        stage=stage,
        message=message,
        progress=progress,
        data=data,
        elapsed_ms=elapsed_ms,
    )
    _store_progress(task_id, event, kind=kind)
    # 首次写入即记录 started_at（用于 elapsed_ms）
    try:
        client = _redis_client()
        client.hsetnx(_meta_key(task_id), "started_at", str(time.time()))
    except Exception:
        # disk fallback
        try:
            p = _meta_path(task_id)
            if p.exists():
                meta = json.loads(p.read_text(encoding="utf-8"))
                if "started_at" not in meta:
                    meta["started_at"] = str(time.time())
                    p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
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
        logger.warning("persist_result redis set failed for %s: %s", task_id, exc)
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
    except Exception as exc:
        log_handled_exception(logger, exc, "handled exception")
    return _file_read_meta(task_id)


def get_task_result(task_id: str) -> dict[str, Any] | None:
    try:
        client = _redis_client()
        raw = client.get(_result_key(task_id))
        if raw:
            return json.loads(raw)
    except Exception as exc:
        log_handled_exception(logger, exc, "handled exception")
    return _file_read_result(task_id)


def task_exists(task_id: str) -> bool:
    if get_task_meta(task_id):
        return True
    try:
        from .tasks import load_persisted_task

        return load_persisted_task(task_id) is not None
    except Exception as exc:
        log_handled_exception(logger, exc, "optional feature check")
        return False


def register_pending(task_id: str, kind: str) -> None:
    publish_progress(
        task_id,
        TaskProgressStatus.PENDING,
        message="queued",
        progress=0.0,
        kind=kind,
    )

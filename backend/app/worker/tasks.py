"""Async task orchestration.

All HTTP-facing async work (search, deep research, optimization, ingestion)
runs through **TaskManager** — a thread-backed registry the API polls via
``GET /api/tasks/{id}``. It works with no Redis or Celery worker, keeping the
MVP self-contained while preserving the async, non-blocking UX.

**Celery tasks** at the bottom of this module (``optimize_task``,
``deep_research_task``, etc.) are legacy mirrors for optional horizontal
scaling when ``docker compose --profile celery up`` is used. The FastAPI routes
never dispatch to Celery directly.

All in-process tasks are persisted to disk on every state update so a uvicorn
``--reload`` (or any process restart) does not cause the frontend poll loop to
receive a permanent 404.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Filesystem fallback so tasks survive a process restart (uvicorn --reload, etc.).
_TASK_PERSIST_DIR = Path(os.environ.get("FORMUMIND_TASK_DIR", "/tmp/formumind_tasks"))


def _persist_task(task_id: str, status: "TaskStatus") -> None:
    """Write task status to disk (best-effort)."""
    try:
        _TASK_PERSIST_DIR.mkdir(parents=True, exist_ok=True)
        data = status.model_dump()
        # TaskState is an enum; serialise to its value so json.dumps works.
        data["state"] = data["state"].value if hasattr(data["state"], "value") else data["state"]
        (_TASK_PERSIST_DIR / f"{task_id}.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass  # persistence is best-effort; never crash background threads


def load_persisted_task(task_id: str) -> "TaskStatus | None":
    """Recover a task result from disk after a process restart."""
    path = _TASK_PERSIST_DIR / f"{task_id}.json"
    if not path.exists():
        return None
    try:
        from ..domain.schemas import TaskStatus as _TS
        data = json.loads(path.read_text(encoding="utf-8"))
        return _TS(**data)
    except Exception:
        return None

from ..domain.schemas import Requirement, TaskState, TaskStatus
from ..pipeline import workflow
from .celery_app import celery_app


class TaskManager:
    """Thread-safe in-process registry of background jobs."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskStatus] = {}
        self._lock = threading.Lock()

    def _register(self, task_id: str, status: TaskStatus) -> None:
        """Store a new task in memory and on disk."""
        with self._lock:
            self._tasks[task_id] = status
        _persist_task(task_id, status)

    def _set(self, task_id: str, **changes: Any) -> None:
        with self._lock:
            status = self._tasks.get(task_id)
            if status is None:
                status = load_persisted_task(task_id)
            if status is None:
                logger.warning("task %s not found for update", task_id)
                return
            updated = status.model_copy(update=changes)
            self._tasks[task_id] = updated
        _persist_task(task_id, updated)

    def get(self, task_id: str) -> TaskStatus | None:
        with self._lock:
            status = self._tasks.get(task_id)
        if status is not None:
            return status
        status = load_persisted_task(task_id)
        if status is not None:
            with self._lock:
                self._tasks[task_id] = status
        return status

    def submit_optimization(
        self,
        req: Requirement,
        iterations: int | None = None,
        *,
        engine: str = "auto",
        campaign_state: str | None = None,
        workbench_campaign_id: int | None = None,
    ) -> str:
        task_id = uuid.uuid4().hex
        self._register(
            task_id,
            TaskStatus(task_id=task_id, kind="optimize", state=TaskState.pending),
        )

        def _run() -> None:
            self._set(task_id, state=TaskState.running, message="starting optimizer")
            try:
                def progress(p: float, msg: str) -> None:
                    self._set(task_id, progress=round(p, 3), message=msg)

                result = workflow.run_optimization(
                    req,
                    iterations=iterations,
                    progress_cb=progress,
                    engine=engine,
                    campaign_state=campaign_state,
                    workbench_campaign_id=workbench_campaign_id,
                )
                self._set(
                    task_id,
                    state=TaskState.completed,
                    progress=1.0,
                    message="done",
                    result=result.model_dump(),
                )
            except Exception as exc:  # surface failures to the poller
                self._set(task_id, state=TaskState.failed, message=str(exc))

        threading.Thread(target=_run, daemon=True).start()
        return task_id

    def submit_loop(
        self,
        req: Requirement,
        iterations: int | None = None,
        n_suggest: int = 4,
        *,
        optimize_engine: str = "auto",
        doe_engine: str = "auto",
    ) -> str:
        """Run one self-driving loop turn (optimize + next-DOE) in the background."""
        from ..services import auto_loop

        task_id = uuid.uuid4().hex
        self._register(
            task_id,
            TaskStatus(task_id=task_id, kind="loop", state=TaskState.pending),
        )

        def _run() -> None:
            self._set(task_id, state=TaskState.running, message="starting loop")
            try:
                def progress(p: float, msg: str) -> None:
                    self._set(task_id, progress=round(p, 3), message=msg)

                result = auto_loop.loop_iterate(
                    req,
                    optimize_iterations=iterations or 24,
                    n_suggest=n_suggest,
                    progress_cb=progress,
                    optimize_engine=optimize_engine,
                    doe_engine=doe_engine,
                )
                self._set(
                    task_id,
                    state=TaskState.completed,
                    progress=1.0,
                    message="done",
                    result=result.model_dump(),
                )
            except Exception as exc:  # surface failures to the poller
                self._set(task_id, state=TaskState.failed, message=str(exc))

        threading.Thread(target=_run, daemon=True).start()
        return task_id

    def submit_comprehensive_research(
        self,
        topic: str,
        req: Requirement | None = None,
        source_types: list[str] | None = None,
    ) -> str:
        """Run deep research in the background (DeepResearchEngine)."""
        from ..services.deep_research import DeepResearchEngine

        task_id = uuid.uuid4().hex
        self._register(
            task_id,
            TaskStatus(task_id=task_id, kind="deep_research", state=TaskState.pending),
        )

        def _run() -> None:
            self._set(task_id, state=TaskState.running, message="starting deep research")
            try:
                def progress(p: float, msg: str) -> None:
                    self._set(task_id, progress=round(p, 3), message=msg)

                def retrieval_progress(partial: list) -> None:
                    self._set(
                        task_id,
                        message=f"已检索 {len(partial)} 条，继续深度研究…",
                        result={
                            "topic": topic,
                            "citations": [e.model_dump() for e in partial],
                            "partial": True,
                        },
                    )

                result = DeepResearchEngine().run(
                    topic,
                    req=req,
                    source_types=source_types,
                    progress_cb=progress,
                    retrieval_progress_cb=retrieval_progress,
                )
                self._set(
                    task_id,
                    state=TaskState.completed,
                    progress=1.0,
                    message="done",
                    result=result.model_dump(),
                )
            except Exception as exc:
                self._set(task_id, state=TaskState.failed, message=str(exc))

        threading.Thread(target=_run, daemon=True).start()
        return task_id

    def submit_search(
        self,
        query: str,
        source_types: list[str],
        req: Requirement | None = None,
        total_limit: int = 300,
        per_source_cap: int = 50,
    ) -> str:
        """Run an incremental multi-source search in the background.

        Results accumulate into ``result.evidence`` as each round completes, so the
        client can poll ``GET /api/tasks/{id}`` and render sources while the search
        keeps going (stops when no source turns up new related results)."""
        from ..services import literature

        task_id = uuid.uuid4().hex
        self._register(
            task_id,
            TaskStatus(task_id=task_id, kind="search", state=TaskState.pending),
        )

        def _run() -> None:
            self._set(task_id, state=TaskState.running, message="检索中…")
            try:
                def progress(partial) -> None:
                    self._set(
                        task_id,
                        message=f"已找到 {len(partial)} 条，继续搜索…",
                        result={
                            "evidence": [e.model_dump() for e in partial],
                            "total": len(partial),
                        },
                    )

                final = literature.iter_search(
                    query,
                    source_types,
                    req=req,
                    total_limit=total_limit,
                    per_source_cap=per_source_cap,
                    progress_cb=progress,
                )
                status = {
                    k: v for k, v in literature.get_source_availability().items()
                }
                self._set(
                    task_id,
                    state=TaskState.completed,
                    progress=1.0,
                    message=f"检索完成，共 {len(final)} 条",
                    result={
                        "evidence": [e.model_dump() for e in final],
                        "total": len(final),
                        "source_status": status,
                    },
                )
            except Exception as exc:  # surface failures to the poller
                self._set(task_id, state=TaskState.failed, message=str(exc))

        threading.Thread(target=_run, daemon=True).start()
        return task_id

    def submit_dependency_install(
        self, names: list[str], upgrade: bool = False
    ) -> str:
        """Run a pip install/upgrade of optional dependencies in the background."""
        from ..services import dependencies as deps

        task_id = uuid.uuid4().hex
        self._register(
            task_id,
            TaskStatus(task_id=task_id, kind="deps", state=TaskState.pending),
        )

        def _run() -> None:
            verb = "upgrading" if upgrade else "installing"
            self._set(task_id, state=TaskState.running, message=f"{verb} {', '.join(names)}")
            try:
                result = deps.install(names, upgrade=upgrade)
                self._set(
                    task_id,
                    state=TaskState.completed if result["ok"] else TaskState.failed,
                    progress=1.0,
                    message=result.get("summary", "done"),
                    result=result,
                )
            except Exception as exc:  # surface failures to the poller
                self._set(task_id, state=TaskState.failed, message=str(exc))

        threading.Thread(target=_run, daemon=True).start()
        return task_id


task_manager = TaskManager()


@celery_app.task(name="formumind.optimize")
def optimize_task(requirement: dict, iterations: int | None = None) -> dict:
    """Celery entry point mirroring the in-process path (for deployed workers)."""
    req = Requirement(**requirement)
    return workflow.run_optimization(req, iterations=iterations).model_dump()


@celery_app.task(name="formumind.ingest_patents")
def ingest_patents_task(requirement: dict) -> dict:
    """Fetch + index prior art (delegates to the research workflow)."""
    req = Requirement(**requirement)
    result = workflow.run_research(req)
    return {"ingested": len(result.evidence)}


@celery_app.task(name="formumind.loop")
def loop_task(requirement: dict, iterations: int | None = None, n_suggest: int = 4) -> dict:
    """Celery entry point for the self-driving loop (for deployed workers)."""
    from ..services import auto_loop

    req = Requirement(**requirement)
    return auto_loop.loop_iterate(
        req, optimize_iterations=iterations or 24, n_suggest=n_suggest
    ).model_dump()


@celery_app.task(name="formumind.deep_research")
def deep_research_task(
    topic: str,
    requirement: dict | None = None,
    source_types: list[str] | None = None,
) -> dict:
    """Celery entry point for deep research (legacy mirror; API uses TaskManager)."""
    from ..services.deep_research import DeepResearchEngine

    req = Requirement(**requirement) if requirement else None
    return DeepResearchEngine().run(topic, req=req, source_types=source_types).model_dump()

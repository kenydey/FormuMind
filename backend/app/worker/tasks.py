"""Async task orchestration.

Long-running work (the optimization loop, patent ingestion) is dispatched here.
Two execution paths share one workflow implementation:

* **Celery tasks** (``optimize_task``) — used when a broker/worker is deployed.
* **In-process TaskManager** — a thread-backed registry the API polls via
  ``GET /api/tasks/{id}``. It works with no Redis/worker, keeping the MVP
  self-contained while preserving the async, non-blocking UX.
"""
from __future__ import annotations

import threading
import uuid
from typing import Any

from ..domain.schemas import Requirement, TaskState, TaskStatus
from ..pipeline import workflow
from .celery_app import celery_app


class TaskManager:
    """Thread-safe in-process registry of background jobs."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskStatus] = {}
        self._lock = threading.Lock()

    def _set(self, task_id: str, **changes: Any) -> None:
        with self._lock:
            status = self._tasks[task_id]
            self._tasks[task_id] = status.model_copy(update=changes)

    def get(self, task_id: str) -> TaskStatus | None:
        with self._lock:
            return self._tasks.get(task_id)

    def submit_optimization(self, req: Requirement, iterations: int | None = None) -> str:
        task_id = uuid.uuid4().hex
        with self._lock:
            self._tasks[task_id] = TaskStatus(
                task_id=task_id, kind="optimize", state=TaskState.pending
            )

        def _run() -> None:
            self._set(task_id, state=TaskState.running, message="starting optimizer")
            try:
                def progress(p: float, msg: str) -> None:
                    self._set(task_id, progress=round(p, 3), message=msg)

                result = workflow.run_optimization(req, iterations=iterations, progress_cb=progress)
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
        self, req: Requirement, iterations: int | None = None, n_suggest: int = 4
    ) -> str:
        """Run one self-driving loop turn (optimize + next-DOE) in the background."""
        from ..services import auto_loop

        task_id = uuid.uuid4().hex
        with self._lock:
            self._tasks[task_id] = TaskStatus(
                task_id=task_id, kind="loop", state=TaskState.pending
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
        self, topic: str, req: Requirement | None = None
    ) -> str:
        """Run a knowledge-cohort deep research pass in the background."""
        from ..services import knowledge_cohort

        task_id = uuid.uuid4().hex
        with self._lock:
            self._tasks[task_id] = TaskStatus(
                task_id=task_id, kind="research", state=TaskState.pending
            )

        def _run() -> None:
            self._set(task_id, state=TaskState.running, message="starting deep research")
            try:
                def progress(p: float, msg: str) -> None:
                    self._set(task_id, progress=round(p, 3), message=msg)

                result = knowledge_cohort.conduct_research(
                    topic, req=req, progress_cb=progress
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
def deep_research_task(topic: str, requirement: dict | None = None) -> dict:
    """Celery entry point for knowledge-cohort deep research (for deployed workers)."""
    from ..services import knowledge_cohort

    req = Requirement(**requirement) if requirement else None
    return knowledge_cohort.conduct_research(topic, req=req).model_dump()

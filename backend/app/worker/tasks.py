"""Celery-backed async task orchestration with Redis Pub/Sub progress (CQRS).

Commands enqueue via ``task.delay()``; clients subscribe to
``GET /api/tasks/{id}/stream`` for SSE progress. ``TaskManager`` keeps a
read-only snapshot compatible with ``GET /api/tasks/{id}``.
"""
from __future__ import annotations

from ..services.errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal
import json
import logging
import os
from pathlib import Path
from typing import Any

from ..domain.schemas import Requirement, TaskState, TaskStatus
from ..pipeline import workflow
from .celery_app import celery_app
from .task_progress import (
    TaskProgressStatus,
    persist_result,
    publish_progress,
    register_pending,
)

logger = logging.getLogger(__name__)

_TASK_PERSIST_DIR = Path(os.environ.get("FORMUMIND_TASK_DIR", "/tmp/formumind_tasks"))


def _persist_task(task_id: str, status: TaskStatus) -> None:
    try:
        _TASK_PERSIST_DIR.mkdir(parents=True, exist_ok=True)
        data = status.model_dump()
        data["state"] = data["state"].value if hasattr(data["state"], "value") else data["state"]
        (_TASK_PERSIST_DIR / f"{task_id}.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:
        log_handled_exception(logger, exc, "handled exception")


def load_persisted_task(task_id: str) -> TaskStatus | None:
    path = _TASK_PERSIST_DIR / f"{task_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return TaskStatus(**data)
    except Exception as exc:
        return degrade_return(logger, exc, "operation failed", None)


def _persist_terminal(
    task_id: str,
    kind: str,
    result: dict[str, Any] | None,
    *,
    failed: bool = False,
    message: str = "",
) -> None:
    """Write terminal task snapshot to disk (works when Redis is unavailable)."""
    status = TaskStatus(
        task_id=task_id,
        kind=kind,
        state=TaskState.failed if failed else TaskState.completed,
        progress=0.0 if failed else 1.0,
        message=message or ("failed" if failed else "done"),
        result=result,
        stream_url=f"/api/tasks/{task_id}/stream",
    )
    _persist_task(task_id, status)
    publish_progress(
        task_id,
        TaskProgressStatus.FAILED if failed else TaskProgressStatus.COMPLETED,
        message=message or ("failed" if failed else "done"),
        progress=0.0 if failed else 1.0,
        data=result,
        kind=kind,
    )


def _status_from_progress(task_id: str, kind: str) -> TaskStatus:
    from .task_progress import get_task_meta, get_task_result

    meta = get_task_meta(task_id) or {}
    result = get_task_result(task_id)
    raw_status = meta.get("status", "PENDING")
    state_map = {
        "PENDING": TaskState.pending,
        "RUNNING": TaskState.running,
        "COMPLETED": TaskState.completed,
        "FAILED": TaskState.failed,
    }
    progress = float(meta.get("progress", 0.0) or 0.0)
    return TaskStatus(
        task_id=task_id,
        kind=kind or meta.get("kind", "unknown"),
        state=state_map.get(raw_status, TaskState.pending),
        progress=progress,
        message=meta.get("message", ""),
        result=result,
        stream_url=f"/api/tasks/{task_id}/stream",
    )


class TaskManager:
    """Registry of Celery task metadata for read-only status snapshots."""

    def __init__(self) -> None:
        self._kinds: dict[str, str] = {}

    def register_celery_task(self, task_id: str, kind: str) -> None:
        self._kinds[task_id] = kind
        existing = load_persisted_task(task_id)
        if existing and existing.state in (TaskState.completed, TaskState.failed):
            return
        register_pending(task_id, kind)
        status = TaskStatus(
            task_id=task_id,
            kind=kind,
            state=TaskState.pending,
            message="queued",
            stream_url=f"/api/tasks/{task_id}/stream",
        )
        _persist_task(task_id, status)

    def get(self, task_id: str) -> TaskStatus | None:
        from .task_progress import get_task_meta

        meta = get_task_meta(task_id)
        kind = self._kinds.get(task_id) or (meta or {}).get("kind", "")
        if meta:
            status = _status_from_progress(task_id, kind)
            _persist_task(task_id, status)
            return status
        persisted = load_persisted_task(task_id)
        if persisted:
            return persisted
        if task_id in self._kinds:
            return TaskStatus(
                task_id=task_id,
                kind=kind,
                state=TaskState.pending,
                message="queued",
                stream_url=f"/api/tasks/{task_id}/stream",
            )
        return None

    def submit_optimization(self, *args, **kwargs) -> str:
        req = args[0] if args else kwargs.get("req")
        payload = {
            "requirement": req.model_dump() if hasattr(req, "model_dump") else req,
            "iterations": kwargs.get("iterations"),
            "engine": kwargs.get("engine", "auto"),
            "campaign_state": kwargs.get("campaign_state"),
            "workbench_campaign_id": kwargs.get("workbench_campaign_id"),
        }
        async_result = run_optimize_task.delay(payload)
        self.register_celery_task(async_result.id, "optimize")
        return async_result.id

    def submit_loop(self, *args, **kwargs) -> str:
        req = args[0] if args else kwargs.get("req")
        payload = {
            "requirement": req.model_dump() if hasattr(req, "model_dump") else req,
            "iterations": kwargs.get("iterations") or 24,
            "n_suggest": kwargs.get("n_suggest", 4),
            "optimize_engine": kwargs.get("optimize_engine", "auto"),
            "doe_engine": kwargs.get("doe_engine", "auto"),
        }
        async_result = run_loop_task.delay(payload)
        self.register_celery_task(async_result.id, "loop")
        return async_result.id

    def submit_comprehensive_research(self, topic: str, req=None, source_types=None) -> str:
        payload = {
            "topic": topic,
            "requirement": req.model_dump() if req else None,
            "sources": [],
            "query": topic,
        }
        async_result = run_deep_research_task.delay(payload)
        self.register_celery_task(async_result.id, "deep_research")
        return async_result.id

    def submit_recommend(self, req, sources=None, query: str = "") -> str:
        payload = {
            "topic": query or (req.headline() if req else ""),
            "requirement": req.model_dump() if hasattr(req, "model_dump") else req,
            "sources": [s.model_dump() if hasattr(s, "model_dump") else s for s in (sources or [])],
            "query": query or (req.headline() if req else ""),
        }
        async_result = run_recommend_task.delay(payload)
        self.register_celery_task(async_result.id, "recommend")
        return async_result.id

    def submit_search(self, query, source_types, req=None, total_limit=300, per_source_cap=50) -> str:
        payload = {
            "query": query,
            "source_types": source_types,
            "requirement": req.model_dump() if req else None,
            "total_limit": total_limit,
            "per_source_cap": per_source_cap,
        }
        async_result = run_search_task.delay(payload)
        self.register_celery_task(async_result.id, "search")
        return async_result.id

    def submit_dependency_install(self, names: list[str], upgrade: bool = False) -> str:
        async_result = run_deps_install_task.delay({"names": names, "upgrade": upgrade})
        self.register_celery_task(async_result.id, "deps")
        return async_result.id


task_manager = TaskManager()


def _progress_cb(task_id: str):
    def cb(stage: str, message: str, partial: dict | None = None) -> None:
        publish_progress(
            task_id,
            TaskProgressStatus.RUNNING,
            stage=stage,
            message=message,
            data=partial,
        )

    return cb


@celery_app.task(bind=True, name="formumind.deep_research")
def run_deep_research_task(self, payload: dict) -> dict:
    from ..domain.schemas import ComprehensiveReport, Evidence, Requirement
    from ..pipeline.research_graph import run_research_graph

    task_id = self.request.id
    publish_progress(task_id, TaskProgressStatus.RUNNING, stage="retrieve", message="正在检索")
    try:
        req = Requirement(**payload["requirement"]) if payload.get("requirement") else None
        topic = payload.get("topic") or (req.headline() if req else "")
        query = payload.get("query") or topic
        sources = [Evidence.model_validate(s) for s in payload.get("sources") or []]

        def graph_progress(stage: str, message: str, partial: dict | None = None) -> None:
            publish_progress(
                task_id,
                TaskProgressStatus.RUNNING,
                stage=stage,
                message=message,
                progress=_stage_progress(stage),
                data=partial,
            )

        state = run_research_graph(
            topic=topic,
            req=req,
            query=query,
            pre_index=sources or None,
            progress_cb=graph_progress,
            mode="deep",
        )
        grounded = state.get("grounded_evidence") or []
        report = ComprehensiveReport(
            topic=topic,
            report_markdown=state.get("report_markdown") or state.get("answer") or "",
            citations=state.get("citations") or grounded,
            candidates=state.get("recommended") or [],
            web_count=0,
            kb_count=len(grounded),
            engine=state.get("recommend_engine") or "offline",
            verified_claims=state.get("verified_claims") or [],
            claim_check_engine="offline",
            claim_check_pass_rate=float(state.get("claim_check_pass_rate") or 1.0),
        )
        result = {
            "report": report.model_dump(),
            "grounded_evidence": [e.model_dump() for e in grounded],
        }
        persist_result(task_id, result, failed=False)
        _persist_terminal(task_id, "deep_research", result)
        return result
    except Exception as exc:
        logger.exception("deep_research task failed")
        err = {"error": str(exc)}
        persist_result(task_id, err, failed=True)
        _persist_terminal(task_id, "deep_research", err, failed=True, message=str(exc))
        raise


def _stage_progress(stage: str) -> float:
    return {
        "retrieve": 0.2,
        "grade": 0.45,
        "fallback": 0.55,
        "generate": 0.7,
        "claim_check": 0.82,
        "regenerate": 0.86,
        "recommend": 0.9,
    }.get(stage, 0.5)


@celery_app.task(bind=True, name="formumind.recommend")
def run_recommend_task(self, payload: dict) -> dict:
    from ..domain.schemas import Evidence, Formulation, Requirement
    from ..pipeline.research_graph import graph_state_to_research_result, run_research_graph

    task_id = self.request.id
    publish_progress(task_id, TaskProgressStatus.RUNNING, stage="retrieve", message="正在检索")
    try:
        req = Requirement(**payload["requirement"]) if payload.get("requirement") else None
        if not req:
            raise ValueError("requirement is required")
        topic = payload.get("topic") or req.headline()
        query = payload.get("query") or topic
        sources = [Evidence.model_validate(s) for s in payload.get("sources") or []]
        base_formulas = [
            Formulation.model_validate(f) for f in (payload.get("base_formulas") or [])
        ] or None

        def graph_progress(stage: str, message: str, partial: dict | None = None) -> None:
            publish_progress(
                task_id,
                TaskProgressStatus.RUNNING,
                stage=stage,
                message=message,
                progress=_stage_progress(stage),
                data=partial,
            )

        state = run_research_graph(
            topic=topic,
            req=req,
            query=query,
            pre_index=sources or None,
            progress_cb=graph_progress,
            mode="recommend",
            modify_prompt=payload.get("modify_prompt") or "",
            base_formulas=base_formulas,
        )
        research = graph_state_to_research_result(state, req)
        result = {"research": research.model_dump()}
        persist_result(task_id, result, failed=False)
        _persist_terminal(task_id, "recommend", result)
        return result
    except Exception as exc:
        logger.exception("recommend task failed")
        err = {"error": str(exc)}
        persist_result(task_id, err, failed=True)
        _persist_terminal(task_id, "recommend", err, failed=True, message=str(exc))
        raise


@celery_app.task(bind=True, name="formumind.optimize")
def run_optimize_task(self, payload: dict) -> dict:
    task_id = self.request.id
    publish_progress(task_id, TaskProgressStatus.RUNNING, message="starting optimizer")

    req = Requirement(**payload["requirement"])

    try:
        def progress(p: float, msg: str) -> None:
            publish_progress(
                task_id,
                TaskProgressStatus.RUNNING,
                message=msg,
                progress=round(p, 3),
            )

        result = workflow.run_optimization(
            req,
            iterations=payload.get("iterations"),
            progress_cb=progress,
            engine=payload.get("engine", "auto"),
            campaign_state=payload.get("campaign_state"),
            workbench_campaign_id=payload.get("workbench_campaign_id"),
        )
        data = result.model_dump()
        persist_result(task_id, data, failed=False)
        _persist_terminal(task_id, "optimize", data)
        return data
    except Exception as exc:
        err = {"error": str(exc)}
        persist_result(task_id, err, failed=True)
        _persist_terminal(task_id, "optimize", err, failed=True, message=str(exc))
        raise


@celery_app.task(bind=True, name="formumind.search")
def run_search_task(self, payload: dict) -> dict:
    from ..services import literature

    task_id = self.request.id
    publish_progress(task_id, TaskProgressStatus.RUNNING, message="检索中…")
    query = payload["query"]
    source_types = payload.get("source_types") or []
    req = Requirement(**payload["requirement"]) if payload.get("requirement") else None

    try:
        def progress(partial, meta=None) -> None:
            meta = meta or {}
            source = meta.get("source")
            new_count = int(meta.get("new_count") or 0)
            done = meta.get("sources_done") or []
            pending = meta.get("sources_pending") or []
            if source:
                msg = f"[{source}] +{new_count} 条（累计 {len(partial)}）"
            elif meta.get("final"):
                msg = f"检索完成，共 {len(partial)} 条"
            else:
                msg = f"已找到 {len(partial)} 条，继续搜索…"
            publish_progress(
                task_id,
                TaskProgressStatus.RUNNING,
                stage=f"search:{source}" if source else "search",
                message=msg,
                data={
                    "evidence": [e.model_dump() for e in partial],
                    "total": len(partial),
                    "source": source,
                    "new_count": new_count,
                    "sources_done": done,
                    "sources_pending": pending,
                },
            )

        final = literature.iter_search(
            query,
            source_types,
            req=req,
            total_limit=payload.get("total_limit", 300),
            per_source_cap=payload.get("per_source_cap", 50),
            progress_cb=progress,
        )
        status = {k: v for k, v in literature.get_source_availability().items()}
        data = {
            "evidence": [e.model_dump() for e in final],
            "total": len(final),
            "source_status": status,
        }
        persist_result(task_id, data, failed=False)
        _persist_terminal(task_id, "search", data)
        return data
    except Exception as exc:
        err = {"error": str(exc)}
        persist_result(task_id, err, failed=True)
        _persist_terminal(task_id, "search", err, failed=True, message=str(exc))
        raise


@celery_app.task(bind=True, name="formumind.loop")
def run_loop_task(self, payload: dict) -> dict:
    from ..services import auto_loop

    task_id = self.request.id
    publish_progress(task_id, TaskProgressStatus.RUNNING, message="starting loop")
    req = Requirement(**payload["requirement"])

    try:
        def progress(p: float, msg: str) -> None:
            publish_progress(
                task_id,
                TaskProgressStatus.RUNNING,
                message=msg,
                progress=round(p, 3),
            )

        result = auto_loop.loop_iterate(
            req,
            optimize_iterations=payload.get("iterations") or 24,
            n_suggest=payload.get("n_suggest", 4),
            progress_cb=progress,
            optimize_engine=payload.get("optimize_engine", "auto"),
            doe_engine=payload.get("doe_engine", "auto"),
        )
        data = result.model_dump()
        persist_result(task_id, data, failed=False)
        _persist_terminal(task_id, "loop", data)
        return data
    except Exception as exc:
        err = {"error": str(exc)}
        persist_result(task_id, err, failed=True)
        _persist_terminal(task_id, "loop", err, failed=True, message=str(exc))
        raise


@celery_app.task(bind=True, name="formumind.deps_install")
def run_deps_install_task(self, payload: dict) -> dict:
    from ..services import dependencies as deps

    task_id = self.request.id
    names = payload["names"]
    upgrade = payload.get("upgrade", False)
    verb = "upgrading" if upgrade else "installing"
    publish_progress(task_id, TaskProgressStatus.RUNNING, message=f"{verb} {', '.join(names)}")

    try:
        result = deps.install(names, upgrade=upgrade)
        failed = not result.get("ok", False)
        persist_result(task_id, result, failed=failed)
        _persist_terminal(
            task_id,
            "deps",
            result,
            failed=failed,
            message=result.get("summary", ""),
        )
        return result
    except Exception as exc:
        err = {"error": str(exc)}
        persist_result(task_id, err, failed=True)
        _persist_terminal(task_id, "deps", err, failed=True, message=str(exc))
        raise


# Legacy names kept for imports
optimize_task = run_optimize_task
loop_task = run_loop_task
deep_research_task = run_deep_research_task
recommend_task = run_recommend_task
ingest_patents_task = run_deep_research_task  # unused alias

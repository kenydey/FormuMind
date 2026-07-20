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
import threading
import uuid
from pathlib import Path
from typing import Any

from ..domain.schemas import DOEPlan, OptimizationResult, Requirement, TaskState, TaskStatus
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
            "workbench_campaign_id": kwargs.get("workbench_campaign_id"),
            "campaign_state": kwargs.get("campaign_state"),
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
        kb_task_id = dispatch_kb_ingest(
            result["grounded_evidence"],
            project_id=(req.project_id if req else None),
        )
        if kb_task_id:
            result["kb_ingest_task_id"] = kb_task_id
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
        grounded = state.get("grounded_evidence") or []
        kb_task_id = dispatch_kb_ingest(
            [e.model_dump() if hasattr(e, "model_dump") else e for e in grounded],
            project_id=(req.project_id if req else None),
        )
        if kb_task_id:
            result["kb_ingest_task_id"] = kb_task_id
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


# ── async KB ingest (search → fulltext → knowledge base, in background) ──────


def _kb_ingest_project_id(payload: dict) -> str | None:
    pid = (payload.get("project_id") or "").strip()
    if pid:
        return pid
    req = payload.get("requirement")
    if isinstance(req, dict):
        return (req.get("project_id") or "").strip() or None
    return None


def _kb_ingest_impl(task_id: str, payload: dict) -> dict:
    """Shared body for the Celery task and the eager-mode background thread."""
    from ..domain.schemas import Evidence
    from ..services import kb_ingest

    evidence = [Evidence.model_validate(e) for e in payload.get("evidence") or []]
    docs_state: dict[str, dict] = {}
    order: list[str] = []

    def status_cb(meta: dict) -> None:
        ident = meta["identifier"]
        if ident not in docs_state:
            order.append(ident)
        docs_state[ident] = dict(meta)
        docs = [docs_state[i] for i in order]
        done = sum(1 for d in docs if d["status"] in kb_ingest.TERMINAL_STATES)
        total = len(docs)
        active = next((d for d in docs if d["status"] in ("fetching", "indexing")), None)
        if active:
            verb = "获取全文" if active["status"] == "fetching" else "解析入库"
            msg = f"知识库构建 {done}/{total} · 正在{verb}：{active['title'][:60]}"
        else:
            msg = f"知识库构建 {done}/{total}"
        publish_progress(
            task_id,
            TaskProgressStatus.RUNNING,
            stage="kb_ingest",
            message=msg,
            progress=(done / total) if total else 0.0,
            data={
                "docs": docs,
                "done": done,
                "total": total,
                "indexed": sum(1 for d in docs if d["status"] == "indexed"),
                "failed": sum(1 for d in docs if d["status"] == "failed"),
            },
        )

    try:
        result = kb_ingest.ingest_evidence_docs(
            evidence,
            status_cb=status_cb,
            project_id=_kb_ingest_project_id(payload),
        )
        summary = (
            f"知识库构建完成：入库 {result['indexed']} 篇"
            + (f"，已在库 {result['skipped']} 篇" if result["skipped"] else "")
            + (f"，失败 {result['failed']} 篇" if result["failed"] else "")
        )
        persist_result(task_id, result, failed=False)
        _persist_terminal(task_id, "kb_ingest", result, message=summary)
        return result
    except Exception as exc:
        logger.exception("kb_ingest task failed")
        err = {"error": str(exc)}
        persist_result(task_id, err, failed=True)
        _persist_terminal(task_id, "kb_ingest", err, failed=True, message=str(exc))
        raise


@celery_app.task(bind=True, name="formumind.kb_ingest")
def run_kb_ingest_task(self, payload: dict) -> dict:
    return _kb_ingest_impl(self.request.id, payload)


def dispatch_kb_ingest(
    evidence_dicts: list[dict], *, project_id: str | None = None
) -> str | None:
    """Fire-and-forget background KB build for freshly searched evidence.

    Returns the ingest task id (for the frontend to stream), or None when the
    feature is off / nothing in the list is fetchable.  With a real broker the
    job goes through Celery; in eager mode ``.delay()`` would run inline and
    stall the parent task, so a daemon thread provides the same non-blocking
    behaviour for single-process deployments.
    """
    from ..config import get_settings
    from ..domain.schemas import Evidence
    from ..services import kb_ingest

    if not kb_ingest.ingest_enabled() or not evidence_dicts:
        return None
    try:
        rows = [Evidence.model_validate(e) for e in evidence_dicts]
        targets = kb_ingest.select_ingest_targets(rows)
    except Exception as exc:
        return degrade_return(logger, exc, "kb_ingest target selection failed", None)
    if not targets:
        return None

    payload = {
        "evidence": [ev.model_dump() for ev, _ in targets],
        "project_id": project_id,
    }
    if get_settings().celery_eager:
        task_id = f"kbingest-{uuid.uuid4().hex[:16]}"
        task_manager.register_celery_task(task_id, "kb_ingest")
        threading.Thread(
            target=lambda: _safe_kb_ingest(task_id, payload),
            name="kb-ingest",
            daemon=True,
        ).start()
        return task_id
    async_result = run_kb_ingest_task.delay(payload)
    task_manager.register_celery_task(async_result.id, "kb_ingest")
    return async_result.id


def _safe_kb_ingest(task_id: str, payload: dict) -> None:
    try:
        _kb_ingest_impl(task_id, payload)
    except Exception as exc:  # already persisted as failed inside the impl
        log_handled_exception(logger, exc, "kb_ingest background thread")


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
            "used_seed_fallback": any(e.is_seed_corpus for e in final),
        }
        # Background KB build: enqueue is non-blocking, so the search stream
        # terminates immediately below and the frontend keeps its results.
        kb_task_id = dispatch_kb_ingest(
            data["evidence"],
            project_id=(req.project_id if req else None),
        )
        if kb_task_id:
            data["kb_ingest_task_id"] = kb_task_id
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
    return run_loop_iterate_impl(self.request.id, payload)


def _parse_optional_model(raw: Any, model_cls: type):
    if not raw:
        return None
    try:
        return model_cls.model_validate(raw)
    except (TypeError, ValueError):
        return None


def _persist_loop_history(campaign_id: int | None, report) -> None:
    if not campaign_id:
        return
    from datetime import UTC, datetime

    from ..db.campaign_store import get_campaign_store

    entry = {
        "round": 0,
        "at": datetime.now(UTC).isoformat(),
        "converged": report.converged,
        "rmse_by_metric": report.rmse_by_metric,
        "engine": report.engine,
        "loop_message": report.loop_message,
    }
    try:
        store = get_campaign_store()
        if hasattr(store, "append_loop_history_sync"):
            store.append_loop_history_sync(campaign_id, entry)
    except Exception as exc:
        log_handled_exception(logger, exc, "persist loop_history")


def run_loop_iterate_impl(task_id: str, payload: dict) -> dict:
    from ..services import auto_loop

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
            workbench_campaign_id=payload.get("workbench_campaign_id"),
            campaign_state=payload.get("campaign_state"),
            prior_rmse_history=payload.get("prior_rmse_history") or [],
            prior_optimization=_parse_optional_model(
                payload.get("prior_optimization"), OptimizationResult
            ),
            prior_next_doe=_parse_optional_model(payload.get("prior_next_doe"), DOEPlan),
        )
        data = result.model_dump()
        _persist_loop_history(payload.get("workbench_campaign_id"), result)
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

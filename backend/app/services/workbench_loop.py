"""Dispatch closed-loop optimize + next-DOE after workbench sync (Phase 3B)."""
from __future__ import annotations

import logging
import threading
import uuid
from typing import Any, Dict, Optional

from ..config import get_settings
from ..domain.schemas import LeverSpec, ProductDomain, Requirement

logger = logging.getLogger(__name__)


def _project_auto_loop_on(project_id: str | None) -> bool:
    """Read workspace.auto_loop_on_sync for a project (best-effort)."""
    pid = (project_id or "").strip()
    if not pid:
        return False
    try:
        from ..db.project_store import get_project_store

        detail = get_project_store().get(pid)
        ws = getattr(detail, "workspace", None) if detail is not None else None
        return bool(getattr(ws, "auto_loop_on_sync", False))
    except Exception as exc:
        logger.debug("project auto_loop_on_sync read failed: %s", exc)
        return False


def should_trigger_loop_after_sync(
    training_ingested: int,
    *,
    trigger_loop: bool | None = None,
    project_id: str | None = None,
) -> bool:
    """Decide whether sync should dispatch a closed-loop task.

    Explicit ``trigger_loop`` wins (Workbench checkbox / API clients).
    When ``None``, fire if **global** ``auto_loop_on_sync`` **or** the
    project's workspace ``auto_loop_on_sync`` is true (Batch B — mirrors
    dossier auto_patch OR semantics). Global default remains False.
    """
    if training_ingested <= 0:
        return False
    if trigger_loop is not None:
        return bool(trigger_loop)
    if bool(get_settings().auto_loop_on_sync):
        return True
    return _project_auto_loop_on(project_id)


def requirement_from_campaign(campaign: Any) -> Requirement:
    """Rebuild a minimal Requirement from frozen campaign metadata."""
    from ..domain.objective_contract import objectives_from_snapshot

    domain = ProductDomain.anticorrosion_coating
    objectives = objectives_from_snapshot(campaign.objectives_snapshot, domain)
    levers: list[LeverSpec] = []
    for item in campaign.lever_snapshot or []:
        try:
            levers.append(LeverSpec(**item))
        except (TypeError, ValueError):
            continue
    return Requirement(
        domain=domain,
        project_id=(campaign.project_id or "").strip(),
        objectives=objectives,
        levers=levers,
    )


def _campaign_loop_context(campaign_id: int) -> tuple[list[dict[str, float]], bool, str]:
    """Return (prior_rmse_history, converged, message) from campaign loop_history."""
    from ..db.campaign_store import get_campaign_store

    settings = get_settings()
    if not settings.loop_convergence_enabled:
        return [], False, ""

    campaign = get_campaign_store().get_campaign_sync(campaign_id)
    if campaign is None:
        return [], False, ""

    history = list(campaign.loop_history or [])
    prior_rmse = [
        dict(entry.get("rmse_by_metric") or {})
        for entry in history
        if entry.get("rmse_by_metric")
    ]
    if history and history[-1].get("converged"):
        msg = str(history[-1].get("loop_message") or "闭环已收敛，建议停止迭代")
        return prior_rmse, True, msg
    return prior_rmse, False, ""


def campaign_loop_status(campaign_id: int) -> dict[str, Any]:
    """Surface loop state for Workbench / Hub: idle|running|converged|paused|failed.

    Derived from ``loop_history`` + Redis pause flag (no new tables).
    ``running`` is inferred when the latest history entry lacks a terminal
    ``converged``/``error`` and a Redis pause is not set — callers that know
    an in-flight ``loop_task_id`` may override.
    """
    from ..db.campaign_store import get_campaign_store

    camp = get_campaign_store().get_campaign_sync(int(campaign_id))
    if camp is None:
        return {"status": "idle", "rounds": 0, "converged": False, "message": "campaign_missing"}

    history = list(getattr(camp, "loop_history", None) or [])
    rounds = len(history)
    paused = is_doecycle_paused(int(campaign_id))
    last = history[-1] if history else None
    last_rmse = dict((last or {}).get("rmse_by_metric") or {}) if last else {}
    last_error = str((last or {}).get("error") or (last or {}).get("loop_error") or "")
    converged = bool((last or {}).get("converged")) if last else False
    message = str((last or {}).get("loop_message") or "")

    if paused:
        status = "paused"
        if not message:
            message = "DOE 周期已暂停"
    elif last_error:
        status = "failed"
        if not message:
            message = last_error
    elif converged:
        status = "converged"
        if not message:
            message = "闭环已收敛，建议停止迭代"
    elif rounds > 0 and (last or {}).get("running"):
        status = "running"
        if not message:
            message = "闭环任务进行中"
    elif rounds > 0:
        status = "idle"
        if not message:
            message = f"已完成 {rounds} 轮，可继续保存后触发"
    else:
        status = "idle"
        message = message or "尚未启动闭环"

    return {
        "status": status,
        "rounds": rounds,
        "converged": converged,
        "paused": paused,
        "last_rmse_by_metric": last_rmse,
        "last_error": last_error or None,
        "message": message,
        "doe_plan_id": (last or {}).get("doe_plan_id"),
    }


def dispatch_loop_after_sync(
    *,
    training_ingested: int,
    workbench_campaign_id: int,
    requirement: Requirement | None = None,
    trigger_loop: bool | None = None,
    optimize_engine: str = "auto",
    doe_engine: str = "auto",
    campaign_state: str | None = None,
    n_suggest: int = 4,
    project_id: str | None = None,
) -> tuple[str | None, str]:
    """Optionally fire closed-loop after sync; returns (task_id, user message)."""
    from ..db.campaign_store import get_campaign_store

    campaign = get_campaign_store().get_campaign_sync(workbench_campaign_id)
    pid = (project_id or "").strip() or (
        str(getattr(campaign, "project_id", "") or "").strip() if campaign is not None else ""
    )

    if not should_trigger_loop_after_sync(
        training_ingested, trigger_loop=trigger_loop, project_id=pid or None
    ):
        return None, ""

    if is_doecycle_paused(workbench_campaign_id):
        return None, "闭环未启动：DOE 周期已暂停"

    prior_rmse, converged, conv_msg = _campaign_loop_context(workbench_campaign_id)
    if converged:
        return None, conv_msg

    req = requirement
    if req is None:
        if campaign is None:
            return None, "闭环未启动：Campaign 不存在"
        req = requirement_from_campaign(campaign)

    task_id = _start_loop_task(
        req,
        workbench_campaign_id=workbench_campaign_id,
        campaign_state=campaign_state,
        optimize_engine=optimize_engine,
        doe_engine=doe_engine,
        n_suggest=n_suggest,
        prior_rmse_history=prior_rmse,
    )
    # P4.2: optional dossier S6/S7 patch when loop starts (default OFF).
    try:
        from .wiki.dossier import notify_dossier_event_for_campaign

        notify_dossier_event_for_campaign(workbench_campaign_id, "loop_updated")
    except Exception:
        pass
    return task_id, "已启动闭环：优化收敛分析 + 下一轮 DOE 建议"


def _start_loop_task(
    requirement: Requirement,
    *,
    workbench_campaign_id: int,
    campaign_state: str | None = None,
    optimize_engine: str = "auto",
    doe_engine: str = "auto",
    n_suggest: int = 4,
    prior_rmse_history: list[dict[str, float]] | None = None,
) -> str | None:
    """Fire-and-forget closed-loop task; returns task_id for SSE tracking."""
    from ..worker.tasks import task_manager

    settings = get_settings()
    payload = {
        "requirement": requirement.model_dump(),
        "iterations": settings.optimize_iterations,
        "n_suggest": n_suggest,
        "optimize_engine": optimize_engine,
        "doe_engine": doe_engine,
        "workbench_campaign_id": workbench_campaign_id,
        "campaign_state": campaign_state,
        "prior_rmse_history": prior_rmse_history or [],
    }

    if settings.celery_eager:
        task_id = f"loop-{uuid.uuid4().hex[:16]}"
        task_manager.register_celery_task(task_id, "loop")
        threading.Thread(
            target=lambda: _safe_loop(task_id, payload),
            name="workbench-loop",
            daemon=True,
        ).start()
        return task_id

    from ..worker.tasks import run_loop_task

    async_result = run_loop_task.delay(payload)
    task_manager.register_celery_task(async_result.id, "loop")
    return async_result.id


def _safe_loop(task_id: str, payload: dict) -> None:
    from ..worker.tasks import run_loop_iterate_impl
    from .errors import log_handled_exception

    try:
        run_loop_iterate_impl(task_id, payload)
    except Exception as exc:
        log_handled_exception(logger, exc, "workbench loop background thread")
        try:
            from ..worker.tasks import _persist_terminal, persist_result
            err = {"error": str(exc)}
            persist_result(task_id, err, failed=True)
            _persist_terminal(task_id, "loop", err, failed=True, message=str(exc))
        except Exception:
            logger.exception("failed to mark task as failed")


# ── DOE cycle pause/resume hooks ─────────────────────────────────────────
def pause_resume_doecyle(campaign_id: int, is_paused: bool) -> bool:
    """Pause or resume a DOE cycle for a campaign.

    Implemented by setting a flag in Redis that the loop / doe_cycle tasks check.
    """
    try:
        from ..worker.task_progress import _redis_client

        client = _redis_client()
        key = f"doe_cycle:paused:{campaign_id}"
        client.set(key, str(is_paused).lower(), ex=86400)  # 24h TTL
        logger.info("DOE cycle for campaign %s %s", campaign_id, "paused" if is_paused else "resumed")
        return True
    except Exception as exc:
        logger.error("Failed to pause/resume DOE cycle for campaign %s: %s", campaign_id, exc)
        return False


def is_doecycle_paused(campaign_id: int) -> bool:
    """Whether the Redis pause flag is set for ``campaign_id`` (false on Redis errors)."""
    status = get_doecyle_status(campaign_id)
    return bool(status and status.get("isPaused"))


def get_doecyle_status(campaign_id: int) -> Optional[Dict[str, Any]]:
    """Get the current status of a DOE cycle for a campaign.

    Returns: {"isPaused": bool, "lastUpdated": str, ...}
    """
    try:
        from ..worker.task_progress import _redis_client

        client = _redis_client()
        key = f"doe_cycle:paused:{campaign_id}"
        is_paused_str = client.get(key)
        if isinstance(is_paused_str, bytes):
            is_paused_str = is_paused_str.decode("utf-8", errors="replace")
        is_paused = str(is_paused_str).lower() == "true" if is_paused_str is not None else False

        # Get last updated time from the key's TTL or metadata
        ttl = client.ttl(key)
        last_updated = None
        if ttl is not None and ttl > 0:
            # Approximate last updated based on remaining TTL
            from datetime import datetime, timedelta
            last_updated = (datetime.now() - timedelta(seconds=(86400 - ttl))).isoformat()

        return {
            "isPaused": is_paused,
            "lastUpdated": last_updated,
            "campaignId": campaign_id,
        }
    except Exception as exc:
        logger.error("Failed to get DOE cycle status for campaign %s: %s", campaign_id, exc)
        return None

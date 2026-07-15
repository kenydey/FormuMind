"""Dispatch closed-loop optimize + next-DOE after workbench sync (Phase 3B)."""
from __future__ import annotations

import logging
import threading
import uuid
from typing import Any

from ..config import get_settings
from ..domain.schemas import LeverSpec, ProductDomain, Requirement

logger = logging.getLogger(__name__)


def should_trigger_loop_after_sync(
    training_ingested: int,
    *,
    trigger_loop: bool | None = None,
) -> bool:
    if training_ingested <= 0:
        return False
    if trigger_loop is not None:
        return trigger_loop
    return bool(get_settings().auto_loop_on_sync)


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
) -> tuple[str | None, str]:
    """Optionally fire closed-loop after sync; returns (task_id, user message)."""
    if not should_trigger_loop_after_sync(training_ingested, trigger_loop=trigger_loop):
        return None, ""

    req = requirement
    if req is None:
        from ..db.campaign_store import get_campaign_store

        campaign = get_campaign_store().get_campaign_sync(workbench_campaign_id)
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
    )
    return task_id, "已启动闭环：优化收敛分析 + 下一轮 DOE 建议"


def _start_loop_task(
    requirement: Requirement,
    *,
    workbench_campaign_id: int,
    campaign_state: str | None = None,
    optimize_engine: str = "auto",
    doe_engine: str = "auto",
    n_suggest: int = 4,
) -> str | None:
    """Fire-and-forget closed-loop task; returns task_id for SSE tracking."""
    from ..worker.tasks import run_loop_iterate_impl, task_manager

    settings = get_settings()
    payload = {
        "requirement": requirement.model_dump(),
        "iterations": settings.optimize_iterations,
        "n_suggest": n_suggest,
        "optimize_engine": optimize_engine,
        "doe_engine": doe_engine,
        "workbench_campaign_id": workbench_campaign_id,
        "campaign_state": campaign_state,
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

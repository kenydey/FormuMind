"""DOE endpoint: generate an experimental design over key formulation levers,
and export a generated plan as a fill-in worksheet (CSV / XLSX).
v0.5 adds an Active Learning endpoint that flags the most informative runs.
v0.7 adds pydoe / baybe engine selection.
v0.9 adds async closed-loop DOE cycle dispatch (Celery ``formumind.doe_cycle``)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

import logging
import re

from ..domain.schemas import ActiveDoeResult, DOEPlan, ExperimentRecord, Requirement
from ..pipeline import workflow
from ..services import io_export
from ..services.active_learning import active_learning_doe
from ..worker.tasks import run_doe_cycle_task
from ._dispatch import submit
from ._idempotency import enqueue_outbox

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["doe"])

NATIVE_DESIGNS = ["full_factorial", "fractional_factorial", "plackett_burman", "ccd", "lhs"]
# 单一来源：从 pydoe_engine 导入，避免两处定义漂移（A14）
from ..services.engines.pydoe_engine import PYDOE_DESIGNS as _PYDOE_DESIGNS
PYDOE_DESIGNS = list(_PYDOE_DESIGNS)
ALL_DESIGNS = NATIVE_DESIGNS + PYDOE_DESIGNS
DOE_ENGINES = ["auto", "native", "pydoe"]
AL_ENGINES = ["auto", "legacy", "baybe"]


def _persist_doe_plan(
    plan: DOEPlan,
    campaign_id: int | None = None,
    project_id: str | None = None,
) -> None:
    """Best-effort: persist a single DOEPlan to the doe_plans table."""
    try:
        from ..db import doe_plan_store
        from ..db.database import default_session_factory
        from ..db.session_utils import commit_session

        factory = default_session_factory()
        with commit_session(factory) as session:
            doe_plan_store.save(
                session, plan, campaign_id=campaign_id, project_id=project_id
            )
    except Exception as exc:
        logger.warning("persist doe plan failed: %s", exc, exc_info=True)
    # P4.2: optional dossier S4 patch (default OFF).
    try:
        from ..services.wiki.dossier import notify_dossier_event_for_campaign

        notify_dossier_event_for_campaign(campaign_id, "doe_updated")
    except Exception:
        pass


@router.post("/doe", response_model=DOEPlan)
def generate_doe(
    requirement: Requirement,
    design: str = Query("full_factorial"),
    engine: str = Query("auto", enum=DOE_ENGINES),
    n: int | None = Query(None, ge=2, le=200),
    seed: int | None = Query(None, description="随机种子；指定后 LHS 等随机设计可复现"),
    ccd_alpha: str | None = Query(
        None,
        description=(
            "仅 design=ccd：轴距。face（默认）星点落在因子范围的面上，所有 run 都在 [low, high] 内；"
            "inscribed（内切）：旋转设计整体按 1/α 内缩，仍是旋转设计、星点在面上、所有 run 都在范围内（因子点内缩）；"
            "rotatable 为旋转设计，星点超出范围、对应 run 标为 infeasible；也可给一个数值 α。"
        ),
    ),
) -> DOEPlan:
    if design not in ALL_DESIGNS:
        raise HTTPException(status_code=400, detail=f"Unknown design {design!r}")
    try:
        plan = workflow.build_doe(
            requirement, design=design, engine=engine, n=n, seed=seed, ccd_alpha=ccd_alpha
        )
    except ValueError as exc:
        # v9: 离散因子 + 非 full_factorial 设计 fail-closed → 422 而非 500。
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # P2-5: 极小 n 警告（n < 2k 时统计功效不足）
    _k = len(getattr(requirement, "levers", None) or [])
    _pn = len(plan.runs) if plan else 0
    if _k > 0 and _pn < 2 * _k:
        plan.notes = (plan.notes + "\n" if plan.notes else "") + (
            f"提示：实验数 {_pn} 小于 2×因子数（{_k}），统计功效可能不足，建议增加实验数"
        )
    _persist_doe_plan(plan, project_id=requirement.project_id or None)
    return plan


class FactorSuggestResponse(BaseModel):
    factors: list
    count: int


@router.post("/doe/suggest-factors", response_model=FactorSuggestResponse)
def suggest_doe_factors(requirement: Requirement) -> FactorSuggestResponse:
    """AI/KB-assisted DOE factor suggestions from requirement levers + KB parameter space."""
    from ..services.factor_suggest import suggest_factors

    candidates = suggest_factors(requirement)
    return FactorSuggestResponse(factors=[c.model_dump() for c in candidates], count=len(candidates))


class ActiveDoeRequest(Requirement):
    """Request body for active-learning DOE: extends Requirement with optional fields."""

    existing_records: list[ExperimentRecord] = []
    n_suggest: int = Field(default=4, ge=1, le=50)
    doe_design: str = "lhs"
    engine: str = "auto"
    doe_engine: str = "auto"
    campaign_state: str | None = None
    workbench_campaign_id: int | None = None
    budget_remaining: int | None = None
    # v18-13: 加 seed 字段，否则主动学习 DOE 永远不可复现。
    seed: int | None = Field(default=None, description="随机种子，指定后可复现")


@router.post("/doe/active", response_model=ActiveDoeResult)
def active_doe(req: ActiveDoeRequest) -> ActiveDoeResult:
    """Generate a DOE plan with AI-selected most-informative experiments flagged."""
    base_req = Requirement(
        **req.model_dump(
            exclude={
                "existing_records",
                "n_suggest",
                "doe_design",
                "engine",
                "doe_engine",
                "campaign_state",
                "workbench_campaign_id",
                "budget_remaining",
            }
        )
    )
    try:
        result = active_learning_doe(
            req=base_req,
            existing=req.existing_records,
            n_suggest=req.n_suggest,
            design=req.doe_design,
            engine=req.engine,
            campaign_state=req.campaign_state,
            doe_engine=req.doe_engine,
            workbench_campaign_id=req.workbench_campaign_id,
            budget_remaining=req.budget_remaining,
            seed=req.seed,
        )
    except ValueError as exc:
        # v9: 离散因子 + 非 full_factorial 设计 fail-closed → 422 而非 500。
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _persist_doe_plan(
        result.plan,
        campaign_id=req.workbench_campaign_id,
        project_id=req.project_id or None,
    )
    return result


class DoeCycleBody(BaseModel):
    """Async closed-loop DOE cycle: recommend → Baybe/LHS batch → pending experiments."""

    requirement: Requirement
    workbench_campaign_id: int | None = None
    # P2-4: remaining experiment budget; <= 0 hard-stops with a hold stub.
    budget_remaining: int | None = None
    # v16: DOE 冷启动种子。None = 确定性默认 0；整数 = 可复现。
    seed: int | None = None


@router.post("/doe/cycle", status_code=202)
def start_doe_cycle(body: DoeCycleBody) -> JSONResponse:
    """Enqueue one ``formumind.doe_cycle`` job; client follows ``/api/tasks/{id}/stream``."""
    payload = {
        "requirement": body.requirement.model_dump(),
        "workbench_campaign_id": body.workbench_campaign_id,
        "budget_remaining": body.budget_remaining,
        "seed": body.seed,
    }
    outbox_id = enqueue_outbox("doe_cycle", payload)
    return submit(run_doe_cycle_task, payload, "doe_cycle", outbox_id=outbox_id)


class DoeHistoryResponse(BaseModel):
    items: list[dict]
    total: int
    page: int
    page_size: int


class DoeCycleRunItem(BaseModel):
    id: int
    project_id: str = ""
    domain: str = ""
    engine: str = ""
    prior_measurement_count: int = 0
    experiment_count: int = 0
    status: str = ""
    created_at: str = ""
    # P2-4: objective tracking per cycle.
    best_objective_value: float | None = None
    target_value: float | None = None
    objective_metric: str = ""
    objective_direction: str = ""
    convergence_reason: str = ""
    # P2-4: signed distance to target (<= 0 means target met/exceeded).
    distance_to_target: float | None = None


def _distance_to_target(
    best: float | None, target: float | None, direction: str
) -> float | None:
    """Signed gap remaining: maximize → target - best; minimize → best - target."""
    if best is None or target is None:
        return None
    if (direction or "maximize") == "minimize":
        return best - target
    return target - best


class DoeCycleRunsResponse(BaseModel):
    items: list[DoeCycleRunItem] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)


@router.get("/doe/cycle-runs", response_model=DoeCycleRunsResponse)
def doe_cycle_runs(
    project_id: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
) -> DoeCycleRunsResponse:
    """Wave 3-2: closed-loop cycle execution history for observability.

    ``project_id`` is required (fail-closed): cycle stats are meaningless
    and potentially cross-project without it.
    """
    if not project_id.strip():
        raise HTTPException(status_code=422, detail="project_id must be non-empty")
    from ..db.database import default_session_factory
    from ..db.models import DOECycleRunRow

    factory = default_session_factory()
    with factory() as session:
        rows = (
            session.query(DOECycleRunRow)
            .filter(DOECycleRunRow.project_id == project_id)
            .order_by(DOECycleRunRow.id.desc())
            .limit(limit)
            .all()
        )
        items = [
            DoeCycleRunItem(
                id=r.id,
                project_id=r.project_id,
                domain=r.domain,
                engine=r.engine,
                prior_measurement_count=r.prior_measurement_count,
                experiment_count=r.experiment_count,
                status=r.status,
                created_at=r.created_at.isoformat() if r.created_at else "",
                best_objective_value=r.best_objective_value,
                target_value=r.target_value,
                objective_metric=r.objective_metric or "",
                objective_direction=r.objective_direction or "",
                convergence_reason=r.convergence_reason or "",
                distance_to_target=_distance_to_target(
                    r.best_objective_value,
                    r.target_value,
                    r.objective_direction or "",
                ),
            )
            for r in rows
        ]
        cycle_count = (
            session.query(DOECycleRunRow)
            .filter(DOECycleRunRow.project_id == project_id)
            .count()
        )
    total_experiments = sum(i.experiment_count for i in items)
    last = items[0] if items else None
    # 累计测量数：registry 中本项目已有测量（跨 domain 求和，fail-open）。
    measured_count = 0
    try:
        from ..domain.schemas import ProductDomain
        from ..services.training import registry

        for d in ProductDomain:
            try:
                measured_count += len(registry.records_for(d, project_id=project_id))
            except Exception:
                continue
    except Exception as exc:
        logger.warning("cycle-runs measured_count failed: %s", exc)
    return DoeCycleRunsResponse(
        items=items,
        summary={
            "cycle_count": cycle_count,
            "total_experiments": total_experiments,
            "measured_count": measured_count,
            "last_engine": last.engine if last else "",
            "last_status": last.status if last else "",
            # P2-4: distance-to-target trend, oldest → newest.
            "target_trend": [
                {"id": i.id, "distance_to_target": i.distance_to_target}
                for i in reversed(items)
            ],
            "last_distance_to_target": last.distance_to_target if last else None,
            "last_convergence_reason": last.convergence_reason if last else "",
        },
    )


@router.get("/doe/history", response_model=DoeHistoryResponse)
def doe_history(
    project_id: str = Query(...),
    campaign_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> DoeHistoryResponse:
    """分页查询历史 DOE 记录（最新优先）。

    ``project_id`` is required (fail-closed): plan stats are meaningless
    without a project scope, and unscoped reads would leak plans across
    projects. ``campaign_id`` further narrows to one workbench campaign.
    """
    if not project_id.strip():
        raise HTTPException(status_code=422, detail="project_id must be non-empty")
    from ..db import doe_plan_store
    from ..db.database import default_session_factory

    factory = default_session_factory()
    with factory() as session:
        items, total = doe_plan_store.list_history(
            session,
            campaign_id=campaign_id,
            project_id=project_id,
            page=page,
            page_size=page_size,
        )
    return DoeHistoryResponse(items=items, total=total, page=page, page_size=page_size)


class DoeAbortBody(BaseModel):
    reason: str = ""


def _transition_plan(plan_id: str, new_status: str) -> dict:
    """Thin API wrapper over doe_plan_store.set_status (transition rules live in the store)."""
    from ..db import doe_plan_store
    from ..db.database import default_session_factory
    from ..db.session_utils import commit_session

    factory = default_session_factory()
    try:
        with commit_session(factory) as session:
            status = doe_plan_store.set_status(session, plan_id, new_status)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"plan_id": plan_id, "status": status}


@router.post("/doe/{plan_id}/activate")
def activate_doe_plan(plan_id: str) -> dict:
    """C-4b: draft → active (start executing the plan's experiments)."""
    return _transition_plan(plan_id, "active")


@router.post("/doe/{plan_id}/complete")
def complete_doe_plan(plan_id: str) -> dict:
    """C-4b: active → completed (all runs measured or manually confirmed)."""
    return _transition_plan(plan_id, "completed")


@router.post("/doe/{plan_id}/abort")
def abort_doe_plan(plan_id: str, body: DoeAbortBody) -> dict:
    """C-4b: draft/active → aborted (body carries the reason)."""
    result = _transition_plan(plan_id, "aborted")
    result["reason"] = body.reason
    return result


@router.get("/doe/{plan_id}/export")
def export_doe(plan_id: str, format: str = Query("csv", enum=["csv", "xlsx"])) -> Response:
    """Export a previously generated DOE plan as a fill-in worksheet."""
    plan = workflow.get_cached_plan(plan_id)
    if plan is None:
        # fallback：从 doe_plans 表读（重启后内存缓存丢失仍可导出）
        from ..db import doe_plan_store
        from ..db.database import default_session_factory

        factory = default_session_factory()
        with factory() as session:
            plan = doe_plan_store.load(session, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"DOE plan {plan_id} not found.")

    metrics = [workflow.OBJECTIVE[plan.domain]] if plan.domain else []
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", plan_id)[:8]
    filename = f"doe_{plan.design}_{safe_id}"

    if format == "csv":
        body = io_export.plan_to_csv(plan, metrics)
        return Response(
            content=body,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
        )

    try:
        data = io_export.plan_to_xlsx(plan, metrics)
    except RuntimeError as exc:
        logger.exception("doe export xlsx failed")
        raise HTTPException(status_code=503, detail="DOE操作失败") from exc
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}.xlsx"'},
    )

"""DOE endpoint: generate an experimental design over key formulation levers,
and export a generated plan as a fill-in worksheet (CSV / XLSX).
v0.5 adds an Active Learning endpoint that flags the most informative runs.
v0.7 adds pydoe / baybe engine selection."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

import logging
import json
import re
import uuid

from ..domain.schemas import ActiveDoeResult, DOEPlan, ExperimentRecord, Requirement
from ..pipeline import workflow
from ..services import io_export
from ..services.active_learning import active_learning_doe
from ..services.engines.baybe_engine import BaybeCampaignEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["doe"])

NATIVE_DESIGNS = ["full_factorial", "fractional_factorial", "plackett_burman", "ccd", "lhs"]
# 单一来源：从 pydoe_engine 导入，避免两处定义漂移（A14）
from ..services.engines.pydoe_engine import PYDOE_DESIGNS as _PYDOE_DESIGNS
PYDOE_DESIGNS = list(_PYDOE_DESIGNS)
ALL_DESIGNS = NATIVE_DESIGNS + PYDOE_DESIGNS
DOE_ENGINES = ["auto", "native", "pydoe"]
AL_ENGINES = ["auto", "legacy", "baybe"]


def _persist_doe_plan(plan: DOEPlan, campaign_id: int | None = None) -> None:
    """Best-effort: persist a single DOEPlan to the doe_plans table."""
    try:
        from ..db import doe_plan_store
        from ..db.database import default_session_factory
        from ..db.session_utils import commit_session

        factory = default_session_factory()
        with commit_session(factory) as session:
            doe_plan_store.save(session, plan, campaign_id=campaign_id)
    except Exception as exc:
        logger.warning("persist doe plan failed: %s", exc, exc_info=True)


@router.post("/doe", response_model=DOEPlan)
def generate_doe(
    requirement: Requirement,
    design: str = Query("full_factorial"),
    engine: str = Query("auto", enum=DOE_ENGINES),
    n: int | None = Query(None, ge=2, le=200),
) -> DOEPlan:
    if design not in ALL_DESIGNS and design not in NATIVE_DESIGNS:
        raise HTTPException(status_code=400, detail=f"Unknown design {design!r}")
    plan = workflow.build_doe(requirement, design=design, engine=engine, n=n)
    _persist_doe_plan(plan)
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
    )
    _persist_doe_plan(result.plan, campaign_id=req.workbench_campaign_id)
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


class DoeHistoryResponse(BaseModel):
    items: list[dict]
    total: int
    page: int
    page_size: int


@router.get("/doe/history", response_model=DoeHistoryResponse)
def doe_history(
    campaign_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> DoeHistoryResponse:
    """分页查询历史 DOE 记录（最新优先）。

    ``campaign_id`` 缺省时返回全部（含未关联的孤立记录）。
    """
    from ..db import doe_plan_store
    from ..db.database import default_session_factory

    factory = default_session_factory()
    with factory() as session:
        items, total = doe_plan_store.list_history(
            session, campaign_id=campaign_id, page=page, page_size=page_size
        )
    return DoeHistoryResponse(items=items, total=total, page=page, page_size=page_size)

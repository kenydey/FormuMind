"""DOE endpoint: generate an experimental design over key formulation levers,
and export a generated plan as a fill-in worksheet (CSV / XLSX).
v0.5 adds an Active Learning endpoint that flags the most informative runs.
v0.7 adds pydoe / baybe engine selection."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

import logging
import re
import uuid

from ..domain.schemas import ActiveDoeResult, BaybeRecommendResult, DOEPlan, ExperimentRecord, Requirement
from ..pipeline import workflow
from ..services import io_export
from ..services.active_learning import active_learning_doe
from ..services.engines.baybe_engine import BaybeCampaignEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["doe"])

NATIVE_DESIGNS = ["full_factorial", "fractional_factorial", "plackett_burman", "ccd", "lhs"]
PYDOE_DESIGNS = ["bbdesign", "simplex_lattice", "sobol"]
ALL_DESIGNS = NATIVE_DESIGNS + PYDOE_DESIGNS
DOE_ENGINES = ["auto", "native", "pydoe"]
AL_ENGINES = ["auto", "legacy", "baybe"]


def _persist_doe_plan(plan: DOEPlan) -> None:
    """Best-effort: persist a single DOEPlan to the doe_plans table."""
    try:
        from ..db import doe_plan_store
        from ..db.database import default_session_factory

        factory = default_session_factory()
        with factory() as session:
            doe_plan_store.save(session, plan)
            session.commit()
    except Exception as exc:
        logger.warning("persist doe plan failed: %s", exc, exc_info=True)


def _persist_run_record(
    operation: str, request_data: dict, result: BaybeRecommendResult,
) -> None:
    """Best-effort: enqueue baybe run record to task_outbox (idempotent audit).

    Uses content-addressed idempotency derived from the request parameters so
    retries with the same payload collapse onto a single outbox row.
    """
    try:
        import hashlib
        import json

        from ..db import outbox_store
        from ..db.database import default_session_factory

        raw = json.dumps(request_data, sort_keys=True, ensure_ascii=False)
        idempotency_key = hashlib.sha256(raw.encode()).hexdigest()

        plan = result.plan
        payload: dict = {
            "plan_id": plan.plan_id,
            "domain": plan.domain.value if plan.domain else None,
            "design": plan.design,
            "suggestions_count": len(plan.runs),
            "factors": [f.name for f in plan.factors],
            "engine": getattr(result, "engine", "baybe"),
            "campaign_state": (
                result.campaign_state[:100] if result.campaign_state else ""
            ),
        }

        factory = default_session_factory()
        with factory() as session:
            outbox_store.enqueue(
                session,
                operation=operation,
                idempotency_key=idempotency_key,
                payload=payload,
            )
            session.commit()
    except Exception as exc:
        logger.warning("persist run record failed: %s", exc, exc_info=True)


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


class BaybeRecommendRequest(Requirement):
    """Stateless baybe recommend roundtrip."""

    existing_records: list[ExperimentRecord] = []
    batch_size: int = Field(default=4, ge=1, le=50)
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
    _persist_doe_plan(result.plan)
    return result


@router.post("/baybe/recommend", response_model=BaybeRecommendResult)
def baybe_recommend(req: BaybeRecommendRequest) -> BaybeRecommendResult:
    """Pure baybe Campaign recommendation with JSON state roundtrip."""
    base_req = Requirement(
        **req.model_dump(
            exclude={
                "existing_records",
                "batch_size",
                "campaign_state",
                "workbench_campaign_id",
                "budget_remaining",
            }
        )
    )
    engine = BaybeCampaignEngine()
    if not engine.available():
        raise HTTPException(status_code=503, detail="baybe is not installed")
    result = engine.recommend(
        base_req,
        campaign_state=req.campaign_state,
        measurements=req.existing_records,
        batch_size=req.batch_size,
        workbench_campaign_id=req.workbench_campaign_id,
        budget_remaining=req.budget_remaining,
    )
    result.plan.plan_id = uuid.uuid4().hex
    result.plan.domain = base_req.domain
    workflow._cache_plan(result.plan)
    _persist_doe_plan(result.plan)
    _persist_run_record("baybe_recommend", req.model_dump(), result)
    return result


@router.get("/doe/{plan_id}/export")
def export_doe(plan_id: str, format: str = Query("csv", enum=["csv", "xlsx"])) -> Response:
    """Export a previously generated DOE plan as a fill-in worksheet."""
    plan = workflow.get_cached_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"DOE plan {plan_id} not found (regenerate it).")

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

"""DOE endpoint: generate an experimental design over key formulation levers,
and export a generated plan as a fill-in worksheet (CSV / XLSX).
v0.5 adds an Active Learning endpoint that flags the most informative runs.
v0.7 adds pydoe / baybe engine selection."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

import uuid

from ..db.database import default_session_factory
from ..domain.schemas import ActiveDoeResult, BaybeRecommendResult, DOEPlan, ExperimentRecord, Requirement
from ..pipeline import workflow
from ..services import io_export
from ..services.active_learning import active_learning_doe
from ..services.engines.baybe_engine import BaybeCampaignEngine

router = APIRouter(prefix="/api", tags=["doe"])

NATIVE_DESIGNS = ["full_factorial", "fractional_factorial", "plackett_burman", "ccd", "lhs"]
PYDOE_DESIGNS = ["bbdesign", "simplex_lattice", "sobol"]
ALL_DESIGNS = NATIVE_DESIGNS + PYDOE_DESIGNS
DOE_ENGINES = ["auto", "native", "pydoe"]
AL_ENGINES = ["auto", "legacy", "baybe"]


@router.post("/doe", response_model=DOEPlan)
def generate_doe(
    requirement: Requirement,
    design: str = Query("full_factorial"),
    engine: str = Query("auto", enum=DOE_ENGINES),
    n: int | None = Query(None, ge=2, le=200),
) -> DOEPlan:
    if design not in ALL_DESIGNS and design not in NATIVE_DESIGNS:
        raise HTTPException(status_code=400, detail=f"Unknown design {design!r}")
    return workflow.build_doe(requirement, design=design, engine=engine, n=n)


class ActiveDoeRequest(Requirement):
    """Request body for active-learning DOE: extends Requirement with optional fields."""

    existing_records: list[ExperimentRecord] = []
    n_suggest: int = 4
    doe_design: str = "lhs"
    engine: str = "auto"
    doe_engine: str = "auto"
    campaign_state: str | None = None
    workbench_campaign_id: int | None = None


class BaybeRecommendRequest(Requirement):
    """Stateless baybe recommend roundtrip."""

    existing_records: list[ExperimentRecord] = []
    batch_size: int = 4
    campaign_state: str | None = None
    workbench_campaign_id: int | None = None


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
            }
        )
    )
    return active_learning_doe(
        req=base_req,
        existing=req.existing_records,
        n_suggest=req.n_suggest,
        design=req.doe_design,
        engine=req.engine,
        campaign_state=req.campaign_state,
        doe_engine=req.doe_engine,
        workbench_campaign_id=req.workbench_campaign_id,
    )


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
            }
        )
    )
    engine = BaybeCampaignEngine()
    if not engine.available():
        raise HTTPException(status_code=503, detail="baybe is not installed")
    with default_session_factory()() as db:
        result = engine.recommend(
            base_req,
            campaign_state=req.campaign_state,
            measurements=req.existing_records,
            batch_size=req.batch_size,
            workbench_campaign_id=req.workbench_campaign_id,
            db=db,
        )
    result.plan.plan_id = uuid.uuid4().hex
    result.plan.domain = base_req.domain
    workflow._cache_plan(result.plan)
    return result


@router.get("/doe/{plan_id}/export")
def export_doe(plan_id: str, format: str = Query("csv", enum=["csv", "xlsx"])) -> Response:
    """Export a previously generated DOE plan as a fill-in worksheet."""
    plan = workflow.get_cached_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"DOE plan {plan_id} not found (regenerate it).")

    metrics = [workflow.OBJECTIVE[plan.domain]] if plan.domain else []
    filename = f"doe_{plan.design}_{plan_id[:8]}"

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
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}.xlsx"'},
    )

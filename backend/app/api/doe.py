"""DOE endpoint: generate an experimental design over key formulation levers,
and export a generated plan as a fill-in worksheet (CSV / XLSX).
v0.5 adds an Active Learning endpoint that flags the most informative runs."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from ..domain.schemas import DOEPlan, ExperimentRecord, Requirement
from ..pipeline import workflow
from ..services import io_export
from ..services.active_learning import active_learning_doe

router = APIRouter(prefix="/api", tags=["doe"])

DESIGNS = ["full_factorial", "fractional_factorial", "plackett_burman", "ccd", "lhs"]


@router.post("/doe", response_model=DOEPlan)
def generate_doe(
    requirement: Requirement,
    design: str = Query("full_factorial", enum=DESIGNS),
) -> DOEPlan:
    return workflow.build_doe(requirement, design=design)


class ActiveDoeRequest(Requirement):
    """Request body for active-learning DOE: extends Requirement with optional fields."""
    existing_records: list[ExperimentRecord] = []
    n_suggest: int = 4
    doe_design: str = "lhs"


@router.post("/doe/active", response_model=DOEPlan)
def active_doe(req: ActiveDoeRequest) -> DOEPlan:
    """Generate a DOE plan with AI-selected most-informative experiments flagged.

    Requires at least a few existing experiment records for the surrogate to be
    useful. With fewer than 3 records, falls back to empirical predictor scoring.
    ``ai_suggested=true`` rows should be prioritised in the lab queue.
    """
    base_req = Requirement(**req.model_dump(
        exclude={"existing_records", "n_suggest", "doe_design"}
    ))
    plan = active_learning_doe(
        req=base_req,
        existing=req.existing_records,
        n_suggest=req.n_suggest,
        design=req.doe_design,
    )
    return plan


@router.get("/doe/{plan_id}/export")
def export_doe(plan_id: str, format: str = Query("csv", enum=["csv", "xlsx"])) -> Response:
    """Export a previously generated DOE plan as a fill-in worksheet.

    The sheet carries one row per run with natural factor values plus a blank
    ``measured_<metric>`` column for the lab to record results, which can then
    be re-imported via ``POST /api/experiments/import-csv``.
    """
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

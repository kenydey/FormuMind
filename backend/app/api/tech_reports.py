"""Technical report export endpoint (P1-9').

POST /api/reports/export {kind, format, project_id} →
  assemble Markdown → publication_preflight gate (409 on blocking) →
  convert (docx/pdf/html/md) → file download.

Router registration is intentionally NOT done in main.py here; see the
wave-2 report for the two-line snippet the parent agent should add.
"""
from __future__ import annotations

import logging
import re
from typing import Literal

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["reports"])

_REPORT_TITLES = {
    "formulation": "配方技术报告",
    "doe": "DOE技术报告",
    "optimization": "优化技术报告",
}


class TechReportExportRequest(BaseModel):
    kind: Literal["formulation", "doe", "optimization"]
    format: Literal["docx", "pdf", "html", "md"] = "docx"
    project_id: str = Field(min_length=1, max_length=200)


@router.post("/reports/export")
def export_tech_report(body: TechReportExportRequest) -> Response:
    """Assemble a technical report and return it as a downloadable file."""
    from ..config import get_settings
    from ..services.publication_preflight import export_allowed
    from ..services.tech_report import assemble_report
    from ..services.wiki.report_export import export_bytes

    report = assemble_report(body.kind, project_id=body.project_id)
    markdown = report["markdown"]

    settings = get_settings()
    allowed, detail = export_allowed(
        body.project_id, f"tech_report_{body.kind}", markdown, settings=settings
    )
    if not allowed:
        raise HTTPException(
            status_code=409,
            detail={"error": "publication_preflight_blocked", "preflight": detail},
        )

    title = _REPORT_TITLES[body.kind]
    try:
        payload, media_type, ext = export_bytes(markdown, body.format, title=title)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        # e.g. no docx/pdf backend available (no pandoc, no python-docx/fpdf2)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    safe_project = re.sub(r"[^a-zA-Z0-9_-]", "", body.project_id)[:24] or "project"
    filename = f"{body.kind}_report_{safe_project}.{ext}"
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reports/capabilities")
def report_capabilities() -> dict:
    """Which export formats are available in this environment."""
    from ..services.wiki.report_export import export_capabilities

    return export_capabilities()


@router.get("/reports/checklist/{run_id}")
def get_review_checklist(run_id: str) -> dict:
    """Return the structured review checklist for a ReviewRun (P1-32).

    Builds from the persisted run + session dispositions (fail-open);
    404 when the run does not exist.
    """
    from ..services.review_checklist import build_checklist

    checklist = build_checklist(run_id)
    if checklist is None:
        raise HTTPException(status_code=404, detail=f"review run not found: {run_id}")
    return checklist

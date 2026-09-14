"""Build DossierPack — shared context for Project Dossier Wiki and future Report."""
from __future__ import annotations

import logging
from typing import Any

from ...config import get_settings
from ...db.project_store import get_project_store
from .schema import DOSSIER_SECTIONS, utcnow_iso

logger = logging.getLogger(__name__)


def build_project_dossier_pack(
    project_id: str,
    *,
    campaign_id: str | None = None,
) -> dict[str, Any]:
    """Assemble structured pack keyed by ``project_id``.

    P4.1 fills requirements (+ empty shells for later sections). P4.2+ hydrates
    DOE / lab / loop / artifacts from live stores.
    """
    pid = (project_id or "").strip()
    if not pid:
        raise ValueError("project_id required")

    store = get_project_store()
    detail = store.get(pid)
    if detail is None:
        raise LookupError(f"project not found: {pid}")

    ws = detail.workspace
    req = ws.requirement
    requirements_rows = _requirement_rows(req)

    campaign = campaign_id or (
        str(ws.workbench_campaign_id) if ws.workbench_campaign_id is not None else ""
    )

    return {
        "schema_version": 1,
        "template": "project_dossier",
        "project_id": pid,
        "title": detail.title or pid,
        "domain": (req.domain.value if req and req.domain else "") or "",
        "campaign_id": campaign or "",
        "workbench_campaign_id": ws.workbench_campaign_id,
        "updated_at": utcnow_iso(),
        "sections": list(DOSSIER_SECTIONS),
        "requirements": {
            "rows": requirements_rows,
            "substrate": getattr(req, "substrate", None) if req else None,
            "notes": (req.notes or "") if req else "",
            "objectives": [
                o.model_dump(mode="json") if hasattr(o, "model_dump") else dict(o)
                for o in (req.objectives or [])
            ]
            if req
            else [],
            "constraint_values": dict(req.constraint_values or {}) if req else {},
        },
        "literature": {"rows": [], "source_ids": []},
        "formula": {"rows": _formula_rows(ws), "unit_note": "wt% unless stated"},
        "doe": {"plans": [], "runs": []},
        "lab": {"rows": []},
        "loop": {
            "history": list(ws.rmse_history or []),
            "optimization_history": list(ws.optimization_history or []),
            "loop_report": ws.loop_report.model_dump(mode="json") if ws.loop_report else None,
        },
        "artifacts": {"rows": [], "plot_specs": []},
        "flags": {
            "missing_requirement": req is None,
            "empty_literature": True,
            "empty_doe": ws.doe_plan is None,
        },
        "vertical_addendum": (get_settings().wiki_dossier_vertical_addendum or "").strip(),
    }


def _requirement_rows(req) -> list[dict[str, Any]]:
    if req is None:
        return []
    rows: list[dict[str, Any]] = []

    def add(metric: str, value: Any, unit: str, direction: str, source: str = "Requirement") -> None:
        if value is None or value == "":
            return
        rows.append(
            {
                "metric": metric,
                "value": value,
                "unit": unit,
                "direction": direction,
                "source": source,
            }
        )

    add("salt_spray_hours", getattr(req, "salt_spray_hours", None), "h", "maximize")
    add("film_weight_gsm", getattr(req, "film_weight_gsm", None), "g/m^2", "target")
    add("cure_temperature_c", getattr(req, "cure_temperature_c", None), "C", "target")
    add("cleaning_efficiency", getattr(req, "cleaning_efficiency", None), "%", "maximize")
    add("voc_limit_gpl", getattr(req, "voc_limit_gpl", None), "g/L", "upper_bound")
    add("ph_target", getattr(req, "ph_target", None), "", "target")

    for o in req.objectives or []:
        rows.append(
            {
                "metric": getattr(o, "metric", ""),
                "value": getattr(o, "target_value", None)
                if getattr(o, "target_value", None) is not None
                else getattr(o, "ref_max", None),
                "unit": getattr(o, "unit", "") or "",
                "direction": getattr(o, "direction", "") or "",
                "source": "ObjectiveSpec",
            }
        )

    for k, v in (req.constraint_values or {}).items():
        rows.append(
            {
                "metric": str(k),
                "value": v,
                "unit": "",
                "direction": "constraint",
                "source": "constraint_values",
            }
        )
    return rows


def _formula_rows(ws) -> list[dict[str, Any]]:
    form = None
    if ws.requirement and getattr(ws.requirement, "active_formulation", None):
        form = ws.requirement.active_formulation
    elif ws.leaderboard:
        form = ws.leaderboard[0]
    if form is None:
        return []
    rows = []
    for ing in getattr(form, "ingredients", None) or []:
        rows.append(
            {
                "name": getattr(ing, "name", "") or "",
                "role": getattr(ing, "role", "") or getattr(ing, "component_type", "") or "",
                "weight_pct": getattr(ing, "weight_pct", None),
                "cas": getattr(ing, "cas_no", None) or "",
                "source": "active_formulation" if form is getattr(ws.requirement, "active_formulation", None) else "leaderboard",
            }
        )
    return rows

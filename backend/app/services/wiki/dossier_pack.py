"""Build DossierPack — shared context for Project Dossier Wiki and future Report."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from ...config import get_settings
from ...db.project_store import get_project_store
from .schema import DOSSIER_SECTIONS, utcnow_iso

logger = logging.getLogger(__name__)


def build_project_dossier_pack(
    project_id: str,
    *,
    campaign_id: str | None = None,
) -> dict[str, Any]:
    """Assemble structured pack keyed by ``project_id``."""
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
    campaign_int: int | None = None
    try:
        if campaign:
            campaign_int = int(campaign)
    except (TypeError, ValueError):
        campaign_int = ws.workbench_campaign_id

    literature = _literature_slice(pid, ws)
    formula_rows = _formula_rows(ws)
    doe = _doe_slice(ws, campaign_int)
    lab = _lab_slice(pid, ws)
    loop = _loop_slice(ws, campaign_int)
    artifacts = _artifacts_slice(ws, loop, project_id=pid)
    query_drafts = _query_drafts_slice(pid)

    return {
        "schema_version": 1,
        "template": "project_dossier",
        "project_id": pid,
        "title": detail.title or pid,
        "domain": (req.domain.value if req and req.domain else "") or "",
        "campaign_id": str(campaign_int or campaign or ""),
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
        "literature": literature,
        "formula": {"rows": formula_rows, "unit_note": "wt% unless stated"},
        "doe": doe,
        "lab": lab,
        "loop": loop,
        "artifacts": artifacts,
        "query_drafts": query_drafts,
        "flags": {
            "missing_requirement": req is None,
            "empty_literature": not literature.get("rows"),
            "empty_doe": not (doe.get("plans") or doe.get("runs")),
            "empty_lab": not lab.get("rows"),
            "empty_loop": not (loop.get("history") or loop.get("candidates")),
            "pending_query_drafts": bool(query_drafts.get("rows")),
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
    source = "leaderboard"
    if ws.requirement and getattr(ws.requirement, "active_formulation", None):
        form = ws.requirement.active_formulation
        source = "active_formulation"
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
                "source": source,
            }
        )
    return rows


def _literature_slice(project_id: str, ws) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    source_ids: list[str] = []
    try:
        from ...db.source_store import get_source_store

        for doc in get_source_store().list_for_project(project_id, limit=40):
            sid = getattr(doc, "id", "") or ""
            if sid:
                source_ids.append(sid)
            title = (getattr(doc, "title", None) or getattr(doc, "origin_url", None) or sid)[:120]
            guide = getattr(doc, "source_guide", None) or {}
            summary = ""
            if isinstance(guide, dict):
                summary = str(guide.get("summary") or "")[:80]
            rows.append(
                {
                    "cluster": "project_sources",
                    "title": title,
                    "source_id": sid,
                    "snippet": summary,
                    "l1": "",
                }
            )
    except Exception as exc:
        logger.debug("dossier literature hydrate failed: %s", exc)

    # Workspace search hits (Evidence) as secondary rows
    for ev in (ws.sources or [])[:20]:
        ident = getattr(ev, "identifier", "") or ""
        if ident and ident not in source_ids:
            source_ids.append(ident)
        rows.append(
            {
                "cluster": "search_hit",
                "title": (getattr(ev, "title", None) or ident)[:120],
                "source_id": ident,
                "snippet": (getattr(ev, "snippet", None) or "")[:80],
                "l1": "",
            }
        )

    # de-dupe by source_id keeping first
    seen: set[str] = set()
    deduped = []
    for r in rows:
        key = r.get("source_id") or r.get("title") or ""
        if key in seen:
            continue
        seen.add(str(key))
        deduped.append(r)

    return {"rows": deduped[:50], "source_ids": list(dict.fromkeys(source_ids))[:80]}


def _query_drafts_slice(project_id: str) -> dict[str, Any]:
    """S4 ops: unreviewed ``queries/project-{id}-*`` drafts (L2, not Claims)."""
    pid = (project_id or "").strip()
    if not pid:
        return {"rows": [], "count": 0}
    rows: list[dict[str, Any]] = []
    try:
        from ...db.wiki_store import get_wiki_store
        from .schema import safe_key

        prefix = f"queries/project-{safe_key(pid)}-"
        store = get_wiki_store()
        for row in store.list_pages(limit=400):
            path = (row.path or "").replace("\\", "/")
            if not path.startswith(prefix):
                continue
            fl = list(row.flags or [])
            rows.append(
                {
                    "path": path,
                    "title": (row.title or path).strip(),
                    "flags": fl,
                    "unreviewed": "unreviewed" in fl,
                }
            )
            if len(rows) >= 12:
                break
    except Exception as exc:
        logger.debug("dossier query_drafts hydrate failed: %s", exc)
    return {"rows": rows, "count": len(rows)}


def _doe_slice(ws, campaign_id: int | None) -> dict[str, Any]:
    plans: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []

    if ws.doe_plan is not None:
        plan = ws.doe_plan
        factors = []
        for f in plan.factors or []:
            factors.append(
                {
                    "name": getattr(f, "name", ""),
                    "low": getattr(f, "low", None),
                    "high": getattr(f, "high", None),
                    "unit": getattr(f, "unit", "") or "",
                }
            )
        plans.append(
            {
                "round": "",
                "design_type": plan.design,
                "factors": factors,
                "bounds": "; ".join(
                    f"{f['name']}[{f['low']},{f['high']}]{f['unit']}" for f in factors
                ),
                "plan_id": plan.plan_id or "",
                "notes": (plan.notes or "")[:120],
                "source": "workspace.doe_plan",
            }
        )
        for i, run in enumerate(plan.runs or []):
            vals = (
                getattr(run, "values", None)
                or getattr(run, "factor_values", None)
                or getattr(run, "natural", None)
                or getattr(run, "coded", None)
                or {}
            )
            if hasattr(vals, "model_dump"):
                vals = vals.model_dump()
            runs.append(
                {
                    "run": getattr(run, "run_id", None) or i + 1,
                    "factors": vals if isinstance(vals, dict) else {"raw": str(vals)[:80]},
                    "metric": "",
                    "value": "",
                    "method": "",
                    "passed": "",
                    "source": "workspace.doe_plan",
                }
            )

    if campaign_id is not None:
        try:
            from ...db.database import default_session_factory
            from ...db.models import DOEPlanRow

            with default_session_factory()() as session:
                rows = (
                    session.execute(
                        select(DOEPlanRow)
                        .where(DOEPlanRow.campaign_id == int(campaign_id))
                        .order_by(DOEPlanRow.created_at.desc())
                        .limit(20)
                    )
                    .scalars()
                    .all()
                )
            existing_ids = {p.get("plan_id") for p in plans}
            for row in rows:
                if row.id in existing_ids:
                    continue
                params = row.parameters or {}
                factors = params.get("factors") or []
                bounds = ""
                if isinstance(factors, list):
                    parts = []
                    for f in factors:
                        if isinstance(f, dict):
                            parts.append(
                                f"{f.get('name','')}[{f.get('low')},{f.get('high')}]{f.get('unit') or ''}"
                            )
                    bounds = "; ".join(parts)
                plans.append(
                    {
                        "round": row.round if row.round is not None else "",
                        "design_type": row.design_type,
                        "factors": factors if isinstance(factors, list) else [],
                        "bounds": bounds,
                        "plan_id": row.id,
                        "notes": str(params.get("notes") or "")[:120],
                        "source": "doe_plans",
                    }
                )
                for i, run in enumerate(params.get("runs") or []):
                    if not isinstance(run, dict):
                        continue
                    runs.append(
                        {
                            "run": run.get("run_id") or i + 1,
                            "factors": run.get("values") or run.get("factor_values") or {},
                            "metric": "",
                            "value": "",
                            "method": "",
                            "passed": "",
                            "source": row.id,
                        }
                    )
        except Exception as exc:
            logger.debug("dossier doe_plans hydrate failed: %s", exc)

    return {"plans": plans, "runs": runs[:100]}


def _lab_slice(project_id: str, ws=None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    try:
        from ...db.database import default_session_factory
        from ...db.models import ExperimentAttachment, ExperimentRow, SourceDocument

        with default_session_factory()() as session:
            exps = (
                session.execute(
                    select(ExperimentRow)
                    .where(ExperimentRow.project_id == project_id)
                    .order_by(ExperimentRow.created_at.desc())
                    .limit(50)
                )
                .scalars()
                .all()
            )
            exp_ids = [int(e.id) for e in exps]
            att_by_exp: dict[int, list[str]] = {eid: [] for eid in exp_ids}
            if exp_ids:
                atts = session.execute(
                    select(ExperimentAttachment, SourceDocument)
                    .join(
                        SourceDocument,
                        SourceDocument.id == ExperimentAttachment.source_document_id,
                        isouter=True,
                    )
                    .where(ExperimentAttachment.experiment_id.in_(exp_ids))
                    .order_by(ExperimentAttachment.created_at.desc())
                ).all()
                for att, doc in atts:
                    label = ""
                    if doc is not None:
                        label = (doc.filename or doc.title or "").strip()
                    if not label:
                        label = att.source_document_id
                    att_by_exp.setdefault(int(att.experiment_id), []).append(
                        f"{att.kind}:{label}"
                    )

        for exp in exps:
            measured = exp.measured or {}
            if not isinstance(measured, dict):
                measured = {}
            att_cell = "; ".join((att_by_exp.get(int(exp.id)) or [])[:3])
            if measured:
                for metric, value in list(measured.items())[:8]:
                    rows.append(
                        {
                            "at": exp.created_at.isoformat() if exp.created_at else "",
                            "item": exp.item_id or exp.label or str(exp.id),
                            "planned": "",
                            "actual": str(exp.factors or {})[:80],
                            "metric": str(metric),
                            "value": value,
                            "method": "",
                            "attachment": att_cell,
                            "source": f"experiment:{exp.id}",
                        }
                    )
            else:
                rows.append(
                    {
                        "at": exp.created_at.isoformat() if exp.created_at else "",
                        "item": exp.item_id or exp.label or str(exp.id),
                        "planned": "",
                        "actual": str(exp.factors or {})[:80],
                        "metric": "",
                        "value": "",
                        "method": "",
                        "attachment": att_cell,
                        "source": f"experiment:{exp.id}",
                    }
                )
    except Exception as exc:
        logger.debug("dossier lab hydrate failed: %s", exc)

    # Workbench campaign Completed rows → S5 when SQL ExperimentRow empty
    # (e.g. ledger not yet flushed, or project_id mismatch during migration).
    if not rows and ws is not None:
        cid = getattr(ws, "workbench_campaign_id", None)
        if cid is not None:
            try:
                from ...db.campaign_store import get_campaign_store

                for brow in get_campaign_store().get_experiments_sync(int(cid)):
                    measured = getattr(brow, "measurements", None) or {}
                    if not isinstance(measured, dict) or not measured:
                        continue
                    factors = {
                        **(getattr(brow, "planned_params", None) or {}),
                        **(getattr(brow, "actual_params", None) or {}),
                    }
                    for metric, value in list(measured.items())[:8]:
                        if value is None or value == "":
                            continue
                        rows.append(
                            {
                                "at": "",
                                "item": getattr(brow, "item_id", None)
                                or getattr(brow, "label", None)
                                or str(getattr(brow, "id", "")),
                                "planned": str(getattr(brow, "planned_params", None) or "")[:80],
                                "actual": str(factors)[:80],
                                "metric": str(metric),
                                "value": value,
                                "method": "",
                                "attachment": "",
                                "source": f"workbench:{cid}",
                            }
                        )
                        if len(rows) >= 40:
                            break
                    if len(rows) >= 40:
                        break
            except Exception as exc:
                logger.debug("dossier lab workbench hydrate failed: %s", exc)

    # ELN 不可达时：workspace.measured 作为 S5 金样/手测回退（不进 Claims）。
    if not rows and ws is not None:
        measured = getattr(ws, "measured", None) or {}
        if isinstance(measured, dict) and measured:
            factors: dict[str, Any] = {}
            if ws.doe_plan and ws.doe_plan.runs:
                run = ws.doe_plan.runs[0]
                factors = (
                    getattr(run, "natural", None)
                    or getattr(run, "coded", None)
                    or getattr(run, "values", None)
                    or {}
                )
                if hasattr(factors, "model_dump"):
                    factors = factors.model_dump()
            for metric, value in list(measured.items())[:8]:
                if value is None or value == "":
                    continue
                rows.append(
                    {
                        "at": "",
                        "item": "workspace.measured",
                        "planned": str(ws.doe_plan.plan_id if ws.doe_plan else "")[:40],
                        "actual": str(factors)[:80] if factors else "",
                        "metric": str(metric),
                        "value": value,
                        "method": "",
                        "attachment": "",
                        "source": "workspace.measured",
                    }
                )
    return {"rows": rows}


def _loop_slice(ws, campaign_id: int | None) -> dict[str, Any]:
    history: list[dict[str, Any]] = []
    # Prefer campaign.loop_history when available
    if campaign_id is not None:
        try:
            from ...db.campaign_store import get_campaign_store

            camp = get_campaign_store().get_campaign_sync(int(campaign_id))
            if camp is not None:
                for entry in list(camp.loop_history or []):
                    if isinstance(entry, dict):
                        history.append(dict(entry))
        except Exception as exc:
            logger.debug("dossier campaign loop hydrate failed: %s", exc)

    if not history:
        for h in ws.rmse_history or []:
            if isinstance(h, dict):
                history.append(dict(h))
            else:
                history.append({"rmse": h})

    candidates = []
    for form in (ws.leaderboard or [])[:5]:
        candidates.append(
            {
                "name": getattr(form, "name", "") or "",
                "score": getattr(form, "score", None),
                "predicted": dict(getattr(form, "predicted", None) or {}),
            }
        )

    plot_specs = []
    if ws.optimization_history:
        plot_specs.append(
            {
                "id": "optimization_history",
                "type": "line",
                "metric": "objective",
                "series": list(ws.optimization_history),
            }
        )

    return {
        "history": history,
        "optimization_history": list(ws.optimization_history or []),
        "loop_report": ws.loop_report.model_dump(mode="json") if ws.loop_report else None,
        "candidates": candidates,
        "plot_specs": plot_specs,
    }


def _artifacts_slice(ws, loop: dict[str, Any], *, project_id: str = "") -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for spec in loop.get("plot_specs") or []:
        rows.append(
            {
                "name": spec.get("id") or "plot",
                "kind": "plot_spec",
                "uri": "",
                "section": "S6",
                "from": "optimization_history",
            }
        )

    pid = (project_id or "").strip()
    if pid:
        try:
            from ...db.database import default_session_factory
            from ...db.models import ExperimentAttachment, ExperimentRow, SourceDocument

            with default_session_factory()() as session:
                exp_ids = [
                    int(r[0])
                    for r in session.execute(
                        select(ExperimentRow.id).where(ExperimentRow.project_id == pid)
                    ).all()
                ]
                if exp_ids:
                    hits = session.execute(
                        select(ExperimentAttachment, SourceDocument)
                        .join(
                            SourceDocument,
                            SourceDocument.id == ExperimentAttachment.source_document_id,
                            isouter=True,
                        )
                        .where(ExperimentAttachment.experiment_id.in_(exp_ids))
                        .order_by(ExperimentAttachment.created_at.desc())
                        .limit(80)
                    ).all()
                    for att, doc in hits:
                        name = ""
                        if doc is not None:
                            name = (doc.filename or doc.title or "").strip()
                        if not name:
                            name = att.source_document_id
                        rows.append(
                            {
                                "name": name[:120],
                                "kind": att.kind or "attachment",
                                "uri": f"source:{att.source_document_id}",
                                "section": "S7",
                                "from": f"experiment:{att.experiment_id}",
                            }
                        )
        except Exception as exc:
            logger.debug("dossier artifacts hydrate failed: %s", exc)

    return {"rows": rows, "plot_specs": list(loop.get("plot_specs") or [])}

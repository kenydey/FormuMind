"""DOE plan persistence — idempotent save and campaign-scoped load.

``save`` writes one ``DOEPlan`` domain object into the ``doe_plans``
table, swallowing ``IntegrityError`` so repeated saves with the same
``plan_id`` are no-ops.  ``load_for_campaign`` returns all plans
attached to a given campaign, newest first.
"""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..domain.schemas import DOEPlan, DOEFactor, DOERun, ProductDomain
from .models import DOEPlanRow


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _plan_to_row(
    plan: DOEPlan,
    *,
    campaign_id: int | None = None,
    experiment_id: int | None = None,
    round_no: int | None = None,
    project_id: str | None = None,
) -> DOEPlanRow:
    """Serialize a domain ``DOEPlan`` to an ORM row."""
    plan_id = plan.plan_id or uuid.uuid4().hex
    return DOEPlanRow(
        id=plan_id,
        experiment_id=experiment_id,
        campaign_id=campaign_id,
        round=round_no,
        project_id=project_id or None,
        design_type=plan.design,
        parameters={
            "factors": [f.model_dump() for f in plan.factors],
            "runs": [r.model_dump() for r in plan.runs],
            "notes": plan.notes,
            "domain": plan.domain.value if plan.domain else None,
        },
        created_at=_utcnow(),
    )


def _row_to_plan(row: DOEPlanRow) -> DOEPlan:
    """Deserialize an ORM row back to a domain ``DOEPlan``."""
    params: dict = row.parameters or {}
    return DOEPlan(
        design=row.design_type,
        factors=[DOEFactor(**f) for f in params.get("factors", [])],
        runs=[DOERun(**r) for r in params.get("runs", [])],
        notes=params.get("notes", ""),
        plan_id=row.id,
        domain=ProductDomain(params["domain"]) if params.get("domain") else None,
        status=getattr(row, "status", None) or "draft",
    )


# C-4b: plan lifecycle state machine. draft → active → completed;
# draft/active → aborted. This is the *plan* lifecycle; ``doe_cycle_runs.status``
# (success|error) records a cycle execution result and is not mixed in.
_PLAN_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"active", "aborted"},
    "active": {"completed", "aborted"},
    "completed": set(),
    "aborted": set(),
}


def set_status(session: Session, plan_id: str, new_status: str) -> str:
    """Transition a plan's lifecycle status.

    Raises ``LookupError`` when the plan does not exist and ``ValueError``
    on an illegal transition (the API layer maps both to 404/422).
    """
    if new_status not in _PLAN_TRANSITIONS:
        raise ValueError(f"Unknown plan status {new_status!r}")
    row = session.get(DOEPlanRow, plan_id)
    if row is None:
        raise LookupError(f"DOE plan {plan_id} not found")
    current = row.status or "draft"
    if new_status == current:
        return current
    if new_status not in _PLAN_TRANSITIONS[current]:
        raise ValueError(
            f"Illegal plan status transition: {current} → {new_status}"
        )
    row.status = new_status
    session.flush()
    return new_status


def save(
    session: Session,
    plan: DOEPlan,
    *,
    campaign_id: int | None = None,
    experiment_id: int | None = None,
    round_no: int | None = None,
    project_id: str | None = None,
) -> str:
    """Persist *plan* to the ``doe_plans`` table.

    Returns the plan's ``id`` (``plan.plan_id`` if set, otherwise a fresh
    UUID).  If a row with that id already exists the ``IntegrityError``
    is caught inside a savepoint so the caller's pending changes survive
    (idempotent — existing row is left untouched).
    """
    row = _plan_to_row(
        plan,
        campaign_id=campaign_id,
        experiment_id=experiment_id,
        round_no=round_no,
        project_id=project_id,
    )
    sp = session.begin_nested()
    try:
        session.add(row)
        session.flush()
        sp.commit()
    except IntegrityError:
        sp.rollback()
    except OperationalError:
        raise
    return row.id


def load_for_campaign(
    session: Session, campaign_id: int
) -> list[DOEPlan]:
    """Return every ``DOEPlan`` attached to *campaign_id*, newest first."""
    stmt = (
        select(DOEPlanRow)
        .where(DOEPlanRow.campaign_id == campaign_id)
        .order_by(desc(DOEPlanRow.created_at))
    )
    rows = session.execute(stmt).scalars().all()
    return [_row_to_plan(r) for r in rows]


def load(session: Session, plan_id: str) -> DOEPlan | None:
    """Load a single DOEPlan by id from the ``doe_plans`` table."""
    row = session.get(DOEPlanRow, plan_id)
    return _row_to_plan(row) if row else None


def list_history(
    session: Session,
    *,
    campaign_id: int | None = None,
    project_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """Paginated history of DOE plans, newest first. Returns (items, total).

    ``campaign_id=None`` returns every plan (including legacy orphan rows);
    pass a campaign id to scope to one workbench campaign. ``project_id``
    scopes to one project when provided (legacy NULL rows are excluded —
    they are unscoped, not visible under any project).
    """
    q = select(DOEPlanRow)
    if campaign_id is not None:
        q = q.where(DOEPlanRow.campaign_id == campaign_id)
    if project_id is not None:
        q = q.where(DOEPlanRow.project_id == project_id)

    total = int(
        session.execute(select(func.count()).select_from(q.subquery())).scalar() or 0
    )
    rows = session.execute(
        q.order_by(desc(DOEPlanRow.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()

    items: list[dict] = []
    for r in rows:
        params: dict = r.parameters or {}
        items.append(
            {
                "plan_id": r.id,
                "design": r.design_type,
                "domain": params.get("domain"),
                "factors": params.get("factors", []),
                "runs": params.get("runs", []),
                "notes": params.get("notes", ""),
                "campaign_id": r.campaign_id,
                "round": r.round,
                "status": getattr(r, "status", None) or "draft",
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return items, total

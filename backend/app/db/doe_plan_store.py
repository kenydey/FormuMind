"""DOE plan persistence — idempotent save and campaign-scoped load.

``save`` writes one ``DOEPlan`` domain object into the ``doe_plans``
table, swallowing ``IntegrityError`` so repeated saves with the same
``plan_id`` are no-ops.  ``load_for_campaign`` returns all plans
attached to a given campaign, newest first.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domain.schemas import DOEPlan, DOEFactor, DOERun
from .models import DOEPlanRow


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _plan_to_row(plan: DOEPlan) -> DOEPlanRow:
    """Serialize a domain ``DOEPlan`` to an ORM row."""
    plan_id = plan.plan_id or uuid.uuid4().hex
    return DOEPlanRow(
        id=plan_id,
        experiment_id=None,
        campaign_id=None,
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
    )


def save(session: Session, plan: DOEPlan) -> str:
    """Persist *plan* to the ``doe_plans`` table.

    Returns the plan's ``id`` (``plan.plan_id`` if set, otherwise a fresh
    UUID).  If a row with that id already exists the ``IntegrityError``
    is caught inside a savepoint so the caller's pending changes survive.
    """
    row = _plan_to_row(plan)
    sp = session.begin_nested()
    try:
        session.add(row)
        session.flush()
    except IntegrityError:
        sp.rollback()
    return row.id


def load_for_campaign(
    session: Session, campaign_id: str
) -> list[DOEPlan]:
    """Return every ``DOEPlan`` attached to *campaign_id*, newest first."""
    stmt = (
        select(DOEPlanRow)
        .where(DOEPlanRow.campaign_id == campaign_id)
        .order_by(desc(DOEPlanRow.created_at))
    )
    rows = session.execute(stmt).scalars().all()
    return [_row_to_plan(r) for r in rows]

"""Tests for DOEPlan persistence store (Task 1.4).

Verifies the ``doe_plan_store`` roundtrip: save → load, idempotent
re-save, empty load, and campaign-scoped filtering.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.database import make_engine, make_session_factory
from app.db.doe_plan_store import load_for_campaign, save
from app.db.models import DOEPlanRow
from app.domain.schemas import DOEFactor, DOEPlan, DOERun
from tests.alembic_helpers import run_upgrade


@pytest.fixture()
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fresh SQLite DB migrated to head, exposed as an ORM session."""
    db_url = f"sqlite:///{tmp_path}/doe_plan_store.db"
    run_upgrade(db_url, monkeypatch)
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    with factory() as s:
        yield s
    engine.dispose()


def _stub_plan(design: str = "full_factorial", plan_id: str = "plan-1") -> DOEPlan:
    return DOEPlan(
        design=design,
        plan_id=plan_id,
        factors=[
            DOEFactor(name="resin", low=10, high=30, unit="wt%"),
            DOEFactor(name="hardener", low=5, high=15, unit="wt%"),
        ],
        runs=[
            DOERun(run_id=1, coded={"resin": -1, "hardener": -1}, natural={"resin": 10, "hardener": 5}),
            DOERun(run_id=2, coded={"resin": 1, "hardener": -1}, natural={"resin": 30, "hardener": 5}),
        ],
        notes="unit test plan",
    )


def _row_count(session: Session) -> int:
    return len(session.execute(select(DOEPlanRow)).scalars().all())


# ── test_save_and_load_roundtrip ─────────────────────────────────────────────


def test_save_and_load_roundtrip(session: Session) -> None:
    """save → load_for_campaign returns the persisted plan."""
    plan = _stub_plan(plan_id="rp-1")
    save(session, plan)
    session.commit()

    rows = load_for_campaign(session, "c-1")
    # No campaign assigned → no rows returned by campaign filter
    assert rows == []

    # Verify via raw ORM query
    all_rows = session.execute(select(DOEPlanRow)).scalars().all()
    assert len(all_rows) == 1
    assert all_rows[0].id == "rp-1"
    assert all_rows[0].design_type == "full_factorial"
    params = all_rows[0].parameters
    assert len(params["factors"]) == 2
    assert len(params["runs"]) == 2


# ── test_save_idempotent ─────────────────────────────────────────────────────


def test_save_idempotent(session: Session) -> None:
    """save same plan_id twice → IntegrityError swallowed, only 1 row."""
    plan = _stub_plan(plan_id="idem-1")
    save(session, plan)
    session.commit()
    assert _row_count(session) == 1

    plan2 = _stub_plan(plan_id="idem-1", design="ccd")
    save(session, plan2)
    session.commit()
    # IntegrityError caught, rollback on duplicate → still 1 row
    assert _row_count(session) == 1

    row = session.get(DOEPlanRow, "idem-1")
    assert row is not None
    # Original design_type preserved (not overwritten by idempotent re-save)
    assert row.design_type == "full_factorial"


# ── test_load_empty ──────────────────────────────────────────────────────────


def test_load_empty(session: Session) -> None:
    """load_for_campaign on empty table → []."""
    assert load_for_campaign(session, "no-campaign") == []


# ── test_load_for_campaign_filters ───────────────────────────────────────────


def test_load_for_campaign_filters(session: Session) -> None:
    """Two campaigns → each load_for_campaign returns only its own plans."""
    # Direct ORM insert with campaign_id (bypassing save which sets None)
    import uuid
    from app.db.models import DOEPlanRow

    r1 = DOEPlanRow(
        id="ca-1-a",
        campaign_id="c-a",
        design_type="lhs",
        parameters={"factors": [], "runs": []},
    )
    r2 = DOEPlanRow(
        id="ca-1-b",
        campaign_id="c-a",
        design_type="ccd",
        parameters={"factors": [], "runs": []},
    )
    r3 = DOEPlanRow(
        id="cb-1-a",
        campaign_id="c-b",
        design_type="full_factorial",
        parameters={"factors": [], "runs": []},
    )
    session.add_all([r1, r2, r3])
    session.commit()

    plans_a = load_for_campaign(session, "c-a")
    assert len(plans_a) == 2
    assert {p.plan_id for p in plans_a} == {"ca-1-b", "ca-1-a"}  # newest first
    assert plans_a[0].plan_id == "ca-1-b"  # newest created_at first
    assert plans_a[0].design == "ccd"

    plans_b = load_for_campaign(session, "c-b")
    assert len(plans_b) == 1
    assert plans_b[0].plan_id == "cb-1-a"

    plans_x = load_for_campaign(session, "c-x")
    assert plans_x == []

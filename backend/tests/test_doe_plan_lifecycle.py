"""C-4b: doe_plans lifecycle — migration default, state machine, API.

- migrated ``status`` column defaults to ``draft`` (existing rows backfilled)
- legal transitions: draft → active → completed; draft/active → aborted
- illegal transitions raise (store) / 422 (API); unknown plan → 404
- idempotent save does not clobber an existing status
- list_history exposes status
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import doe_plan_store
from app.db.database import make_engine, make_session_factory
from app.db.models import DOEPlanRow
from app.domain.schemas import DOEFactor, DOEPlan, DOERun
from tests.alembic_helpers import run_upgrade


@pytest.fixture()
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path}/doe_lifecycle.db"
    run_upgrade(db_url, monkeypatch)
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    with factory() as s:
        yield s
    engine.dispose()


def _plan(plan_id: str = "plan-lc-1", project_id: str = "proj-a") -> DOEPlan:
    return DOEPlan(
        design="full_factorial",
        plan_id=plan_id,
        factors=[DOEFactor(name="resin", low=10, high=30, unit="wt%")],
        runs=[DOERun(run_id=1, coded={"resin": -1.0}, natural={"resin": 10.0})],
        notes="lifecycle test",
    )


def _save(session: Session, plan_id: str = "plan-lc-1", project_id: str = "proj-a") -> str:
    return doe_plan_store.save(session, _plan(plan_id), project_id=project_id)


# ── migration default ────────────────────────────────────────────────────────


def test_new_row_defaults_to_draft(session: Session) -> None:
    pid = _save(session)
    row = session.get(DOEPlanRow, pid)
    assert row is not None
    assert row.status == "draft"


def test_list_history_exposes_status(session: Session) -> None:
    _save(session)
    items, total = doe_plan_store.list_history(session, project_id="proj-a")
    assert total == 1
    assert items[0]["status"] == "draft"


# ── state machine (store layer) ──────────────────────────────────────────────


def test_legal_transitions(session: Session) -> None:
    pid = _save(session)
    assert doe_plan_store.set_status(session, pid, "active") == "active"
    assert doe_plan_store.set_status(session, pid, "completed") == "completed"


def test_abort_from_draft_and_active(session: Session) -> None:
    pid1 = _save(session, plan_id="plan-abort-1")
    assert doe_plan_store.set_status(session, pid1, "aborted") == "aborted"
    pid2 = _save(session, plan_id="plan-abort-2")
    doe_plan_store.set_status(session, pid2, "active")
    assert doe_plan_store.set_status(session, pid2, "aborted") == "aborted"


def test_illegal_transitions_raise(session: Session) -> None:
    pid = _save(session)
    with pytest.raises(ValueError, match="Illegal plan status transition"):
        doe_plan_store.set_status(session, pid, "completed")  # draft → completed
    doe_plan_store.set_status(session, pid, "active")
    doe_plan_store.set_status(session, pid, "completed")
    with pytest.raises(ValueError, match="Illegal plan status transition"):
        doe_plan_store.set_status(session, pid, "active")  # completed → active
    with pytest.raises(ValueError, match="Illegal plan status transition"):
        doe_plan_store.set_status(session, pid, "aborted")  # completed → aborted


def test_unknown_status_rejected(session: Session) -> None:
    pid = _save(session)
    with pytest.raises(ValueError, match="Unknown plan status"):
        doe_plan_store.set_status(session, pid, "archived")


def test_missing_plan_raises_lookup(session: Session) -> None:
    with pytest.raises(LookupError):
        doe_plan_store.set_status(session, "no-such-plan", "active")


def test_idempotent_save_preserves_status(session: Session) -> None:
    pid = _save(session)
    doe_plan_store.set_status(session, pid, "active")
    # Re-saving the same plan_id is a no-op — the active status must survive.
    doe_plan_store.save(session, _plan("plan-lc-1"), project_id="proj-a")
    row = session.get(DOEPlanRow, pid)
    assert row is not None
    assert row.status == "active"
    assert session.execute(select(DOEPlanRow)).scalars().all().__len__() == 1


def test_row_to_plan_carries_status(session: Session) -> None:
    pid = _save(session)
    doe_plan_store.set_status(session, pid, "active")
    plan = doe_plan_store.load(session, pid)
    assert plan is not None
    assert plan.status == "active"


# ── API layer ────────────────────────────────────────────────────────────────


def test_api_transition_endpoints() -> None:
    """Full HTTP round-trip via TestClient; 422 on illegal, 404 on missing."""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    # Create a plan through the public endpoint so the row exists in the dev DB.
    req_body = {
        "title": "lifecycle api test",
        "description": "d",
        "domain": "surface_treatment",
        "project_id": "proj-lifecycle-api",
    }
    r = client.post("/api/doe?design=full_factorial&engine=native", json=req_body)
    assert r.status_code == 200, r.text
    plan_id = r.json()["plan_id"]
    assert plan_id

    # draft → completed is illegal → 422
    r = client.post(f"/api/doe/{plan_id}/complete")
    assert r.status_code == 422, r.text

    r = client.post(f"/api/doe/{plan_id}/activate")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"

    r = client.post(f"/api/doe/{plan_id}/complete")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"

    # completed → active illegal → 422
    r = client.post(f"/api/doe/{plan_id}/activate")
    assert r.status_code == 422

    # missing plan → 404
    r = client.post("/api/doe/does-not-exist/activate")
    assert r.status_code == 404

    # history exposes status
    r = client.get(
        "/api/doe/history",
        params={"project_id": "proj-lifecycle-api", "page_size": 5},
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    match = [i for i in items if i["plan_id"] == plan_id]
    assert match and match[0]["status"] == "completed"

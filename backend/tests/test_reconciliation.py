"""Task 1.6: Reconciliation tests — mock Datalab HTTP + SQLite task_outbox."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session

from app.db.database import make_engine, make_session_factory
from app.db.models import TaskOutbox
from app.db import reconciliation as rec_mod
from tests.alembic_helpers import run_upgrade


@pytest.fixture()
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    """Fresh SQLite DB migrated to head, exposed as an ORM session."""
    db_url = f"sqlite:///{tmp_path}/rec.db"
    run_upgrade(db_url, monkeypatch)
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    with factory() as s:
        yield s
    engine.dispose()


def _confirmed_row(
    *,
    experiment_id: str,
    operation: str = "research_recommend",
    campaign_id: str | None = None,
) -> TaskOutbox:
    """Minimal CONFIRMED task_outbox row with experiment_id in payload."""
    payload: dict = {"experiment_id": experiment_id}
    if campaign_id:
        payload["campaign_id"] = campaign_id
    return TaskOutbox(
        id=str(uuid.uuid4()),
        operation=operation,
        idempotency_key=str(uuid.uuid4()),
        payload=payload,
        status="CONFIRMED",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )


def _mock_client(monkeypatch: pytest.MonkeyPatch, handler) -> httpx.Client:
    """Install a mock httpx.Client on rec_mod and return the mock client."""
    transport = httpx.MockTransport(handler)
    client = httpx.Client(base_url="http://datalab.test", transport=transport)
    monkeypatch.setattr(rec_mod.httpx, "Client", lambda *a, **kw: client)
    return client


def test_reconcile_empty(session: Session) -> None:
    """Empty task_outbox → matched=0, missing=0, extra=0, errors=0, skipped=0."""
    result = rec_mod.reconcile(session, "http://datalab.test")
    assert result == {"matched": 0, "missing": 0, "extra": 0, "errors": 0, "skipped": 0}


def test_reconcile_all_matched(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock Datalab returns 200 for every item → matched=N."""
    _mock_client(monkeypatch, lambda req: httpx.Response(200, json={"item_data": {"blocks_obj": {}}}))

    rows = [_confirmed_row(experiment_id=f"exp-{i}") for i in range(3)]
    session.add_all(rows)
    session.commit()

    result = rec_mod.reconcile(session, "http://datalab.test")
    assert result["matched"] == 3
    assert result["missing"] == 0
    assert result["errors"] == 0
    assert result["skipped"] == 0


def test_reconcile_missing(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock Datalab returns 404 for every item → missing=N."""
    _mock_client(monkeypatch, lambda req: httpx.Response(404, json={"error": "not found"}))

    rows = [_confirmed_row(experiment_id=f"exp-{i}") for i in range(3)]
    session.add_all(rows)
    session.commit()

    result = rec_mod.reconcile(session, "http://datalab.test")
    assert result["matched"] == 0
    assert result["missing"] == 3
    assert result["errors"] == 0
    assert result["skipped"] == 0


def test_reconcile_mixed(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mix of 200, 404, and items without experiment_id."""
    responses = {"exp-ok": 200, "exp-gone": 404}

    def _handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        item_id = path.rsplit("/", 1)[-1]
        if item_id.startswith("formumind_exp_"):
            exp_id = item_id[len("formumind_exp_"):]
            status = responses.get(exp_id, 500)
            return httpx.Response(status, json={"dummy": True})
        return httpx.Response(404)

    _mock_client(monkeypatch, _handler)

    row_ok = _confirmed_row(experiment_id="exp-ok")
    row_gone = _confirmed_row(experiment_id="exp-gone")
    row_no_exp = TaskOutbox(
        id=str(uuid.uuid4()),
        operation="research_deep",
        idempotency_key=str(uuid.uuid4()),
        payload={"no_experiment_id": True},
        status="CONFIRMED",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add_all([row_ok, row_gone, row_no_exp])
    session.commit()

    result = rec_mod.reconcile(session, "http://datalab.test")
    assert result["matched"] == 1
    assert result["missing"] == 1
    assert result["skipped"] == 1
    assert result["errors"] == 0


def test_reconcile_campaign_filter(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """campaign_id filter only reconciles matching rows."""
    _mock_client(monkeypatch, lambda req: httpx.Response(200, json={"item_data": {"blocks_obj": {}}}))

    row_a = _confirmed_row(experiment_id="exp-a", campaign_id="camp1")
    row_b = _confirmed_row(experiment_id="exp-b", campaign_id="camp1")
    row_c = _confirmed_row(experiment_id="exp-c", campaign_id="camp2")
    session.add_all([row_a, row_b, row_c])
    session.commit()

    result = rec_mod.reconcile(session, "http://datalab.test", campaign_id="camp1")
    assert result["matched"] == 2
    assert result["missing"] == 0
    assert result["errors"] == 0
    assert result["skipped"] == 0


def test_reconcile_skips_pending(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """PENDING rows are never reconciled."""
    _mock_client(monkeypatch, lambda req: httpx.Response(200, json={"item_data": {"blocks_obj": {}}}))

    pending = TaskOutbox(
        id=str(uuid.uuid4()),
        operation="research_recommend",
        idempotency_key=str(uuid.uuid4()),
        payload={"experiment_id": "exp-p"},
        status="PENDING",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add(pending)
    session.commit()

    result = rec_mod.reconcile(session, "http://datalab.test")
    assert result["matched"] == 0
    assert result["missing"] == 0
    assert result["errors"] == 0
    assert result["skipped"] == 0


def test_reconcile_skips_claimed(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAIMED rows are never reconciled."""
    _mock_client(monkeypatch, lambda req: httpx.Response(200, json={"item_data": {"blocks_obj": {}}}))

    claimed = TaskOutbox(
        id=str(uuid.uuid4()),
        operation="research_recommend",
        idempotency_key=str(uuid.uuid4()),
        payload={"experiment_id": "exp-c"},
        status="CLAIMED",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add(claimed)
    session.commit()

    result = rec_mod.reconcile(session, "http://datalab.test")
    assert result["matched"] == 0
    assert result["missing"] == 0
    assert result["errors"] == 0
    assert result["skipped"] == 0

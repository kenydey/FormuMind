"""Tests for baybe optimize/recommend run record persistence (Task 1.5).

POST /baybe/recommend should write a run record to task_outbox with
operation="baybe_recommend", and the same payload enqueued twice must
produce exactly 1 outbox row (idempotency).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import database as db_mod
from app.db.models import TaskOutbox
from tests.alembic_helpers import run_upgrade


# FastAPI app (imported once per module)
from app.main import app  # noqa: E402


# ── helpers ─────────────────────────────────────────────────────────────────

BAYBE_PAYLOAD = {
    "domain": "anticorrosion_coating",
    "substrate": "carbon_steel",
    "salt_spray_hours": 500,
    "levers": [
        {"name": "resin", "low": 10.0, "high": 30.0, "unit": "wt%"},
    ],
    "batch_size": 1,
}


def _outbox_rows(factory) -> list[TaskOutbox]:
    with factory() as s:
        return list(s.execute(select(TaskOutbox)).scalars().all())


# ── test: baybe_recommend writes run record ──────────────────────────────────


def test_optimize_writes_run_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /baybe/recommend → task_outbox has operation='baybe_recommend' row."""
    db_url = f"sqlite:///{tmp_path}/run_outbox_1.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUMIND_DB_URL", db_url)
    db_mod._default.clear()

    with TestClient(app) as client:
        r = client.post("/api/baybe/recommend", json=BAYBE_PAYLOAD)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("plan"), f"missing plan in {body}"
        plan_id = body["plan"].get("plan_id")
        assert plan_id, f"missing plan_id in {body['plan']}"

        factory = db_mod.default_session_factory()
        rows = _outbox_rows(factory)
        matching = [row for row in rows if row.operation == "baybe_recommend"]
        assert len(matching) >= 1, f"no baybe_recommend row, got: {[(r.operation, r.idempotency_key) for r in rows]}"

        row = matching[0]
        assert row.status == "PENDING"
        assert row.idempotency_key
        payload = row.payload
        assert payload.get("plan_id") == plan_id
        assert payload.get("suggestions_count", 0) >= 1
        assert payload.get("engine") == "baybe"


def test_recommend_writes_run_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same as test_optimize — verifies /baybe/recommend write-path."""
    test_optimize_writes_run_record(tmp_path, monkeypatch)


def test_run_record_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same payload enqueued twice → 200 both times, only 1 outbox row."""
    db_url = f"sqlite:///{tmp_path}/run_outbox_2.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUMIND_DB_URL", db_url)
    db_mod._default.clear()

    with TestClient(app) as client:
        r1 = client.post("/api/baybe/recommend", json=BAYBE_PAYLOAD)
        r2 = client.post("/api/baybe/recommend", json=BAYBE_PAYLOAD)
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text

        factory = db_mod.default_session_factory()
        rows = _outbox_rows(factory)
        baybe_rows = [row for row in rows if row.operation == "baybe_recommend"]
        assert len(baybe_rows) == 1, (
            f"expected 1 baybe_recommend outbox row, got {len(baybe_rows)}: "
            f"{[(r.operation, r.idempotency_key[:16]) for r in rows]}"
        )

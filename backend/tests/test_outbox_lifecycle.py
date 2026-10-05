"""Outbox completion mark + dispatch wiring (P1-1).

Before: nothing ever moved a row out of PENDING after the job ran, so every
restart replayed *all* historic research/inverse jobs (5 attempts each, 20
rows per pass, oldest first); ``doe_cycle`` rows had no dispatch branch and
were counted as "re-enqueued" without anything being sent; audit rows
(``ingest_complete``) were replayed as no-ops until marked DEAD.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import dispatcher
from app.db.database import make_engine, make_session_factory
from app.db.dispatcher import DISPATCHABLE_OPERATIONS, recover_stalled
from app.db.models import TaskOutbox
from app.db.outbox_store import (
    enqueue,
    heartbeat,
    idempotency_key,
    mark_done,
    record_done,
    record_task_id,
    touch,
)
from tests.alembic_helpers import run_upgrade


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Migrated SQLite DB that is also the process-default DB."""
    db_url = f"sqlite:///{tmp_path}/outbox_lifecycle.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUMIND_DB_URL", db_url)
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


def _naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _add_row(
    session: Session,
    operation: str,
    payload: dict,
    *,
    age: timedelta = timedelta(hours=1),
    status: str = "PENDING",
    attempts: int = 0,
) -> TaskOutbox:
    row = TaskOutbox(
        id=str(uuid.uuid4()),
        operation=operation,
        idempotency_key=idempotency_key(operation, payload),
        payload=payload,
        status=status,
        attempt_count=attempts,
        created_at=_naive_now() - age,
        updated_at=_naive_now() - age,
    )
    session.add(row)
    session.commit()
    return row


# ── completion mark ─────────────────────────────────────────────────────────


def test_mark_done_flips_only_inflight_rows(db) -> None:
    payload = {"a": 1}
    with db() as s:
        rid, _ = enqueue(s, "research_recommend", idempotency_key("research_recommend", payload), payload)
        s.commit()
        assert mark_done(s, "research_recommend", payload) is True
        s.commit()
        assert s.get(TaskOutbox, rid).status == "DONE"
        # second call: already terminal → untouched
        assert mark_done(s, "research_recommend", payload) is False

        # a DEAD row is never resurrected to DONE
        dead = _add_row(s, "research_deep", {"b": 2}, status="DEAD")
        assert mark_done(s, "research_deep", {"b": 2}) is False
        s.commit()
        assert s.get(TaskOutbox, dead.id).status == "DEAD"


def test_mark_done_matches_regardless_of_list_order(db) -> None:
    """The worker recomputes the key from the payload it received."""
    with db() as s:
        row = _add_row(s, "research_deep", {"sources": [{"i": 1}, {"i": 2}]})
        s.commit()
        assert mark_done(s, "research_deep", {"sources": [{"i": 2}, {"i": 1}]}) is True
        s.commit()
        assert s.get(TaskOutbox, row.id).status == "DONE"


def test_record_done_never_raises_when_db_is_broken(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.db.database.default_session_factory",
        lambda: (_ for _ in ()).throw(RuntimeError("db gone")),
    )
    assert record_done("research_deep", {"x": 1}) is False


def test_resubmitting_a_finished_job_rearms_the_row(db) -> None:
    """Same payload again = fresh work: crash-recoverable again, clock reset."""
    payload = {"topic": "t"}
    key = idempotency_key("research_deep", payload)
    with db() as s:
        row = _add_row(
            s, "research_deep", payload, age=timedelta(days=3), status="DONE", attempts=4
        )
        rid, status = enqueue(s, "research_deep", key, payload)
        s.commit()
        assert (rid, status) == (row.id, "PENDING")
        fresh = s.get(TaskOutbox, row.id)
        assert fresh.status == "PENDING"
        assert fresh.attempt_count == 0
        assert fresh.created_at > _naive_now() - timedelta(minutes=1)
        # and it is a single row, not a duplicate
        assert len(s.execute(select(TaskOutbox)).scalars().all()) == 1


def test_enqueue_can_record_a_born_done_audit_row(db) -> None:
    with db() as s:
        rid, status = enqueue(s, "ingest_complete", "src-1", {"source_id": "src-1"}, status="DONE")
        s.commit()
        assert status == "DONE"
        assert s.get(TaskOutbox, rid).status == "DONE"
        # a repeat with the same key is idempotent and stays DONE
        rid2, status2 = enqueue(s, "ingest_complete", "src-1", {"source_id": "src-1"}, status="DONE")
        assert (rid2, status2) == (rid, "DONE")


# ── recovery: only unfinished, dispatchable, reasonably fresh rows ──────────


def test_every_operation_the_api_enqueues_has_a_dispatch_handler() -> None:
    """Pins the wiring: an api/* enqueue_outbox('<op>') with no handler would
    be silently skipped on recovery (the doe_cycle regression)."""
    import re

    api_dir = Path(__file__).resolve().parents[1] / "app" / "api"
    ops: set[str] = set()
    for py in api_dir.glob("*.py"):
        ops.update(re.findall(r"enqueue_outbox\(\s*[\"']([a-z_]+)[\"']", py.read_text(encoding="utf-8")))
    assert ops, "expected api modules to enqueue outbox operations"
    assert ops <= set(DISPATCHABLE_OPERATIONS), ops - set(DISPATCHABLE_OPERATIONS)


def test_recovery_dispatches_doe_cycle_to_its_celery_task(db, monkeypatch) -> None:
    from app.worker import tasks as worker_tasks

    sent = MagicMock()
    monkeypatch.setattr(worker_tasks.run_doe_cycle_task, "delay", sent)
    payload = {"requirement": {"product_name": "x"}, "workbench_campaign_id": 3, "budget_remaining": 5}
    with db() as s:
        row = _add_row(s, "doe_cycle", payload)
        assert recover_stalled(s, cutoff_minutes=30) == 1
        s.commit()
        sent.assert_called_once_with(payload)
        assert s.get(TaskOutbox, row.id).attempt_count == 1


def test_unknown_operation_raises_instead_of_counting_as_sent() -> None:
    with pytest.raises(ValueError):
        dispatcher._dispatch("no_such_operation", {})


def test_recovery_ignores_done_and_audit_rows(db, monkeypatch) -> None:
    stub = MagicMock()
    monkeypatch.setattr(dispatcher, "_dispatch", stub)
    with db() as s:
        done = _add_row(s, "research_recommend", {"k": 1}, status="DONE")
        audit = _add_row(s, "ingest_complete", {"source_id": "s"})
        orphan = _add_row(s, "datalab_orphan_cleanup", {"item_id": "i"})
        assert recover_stalled(s, cutoff_minutes=30) == 0
        s.commit()
        stub.assert_not_called()
        # untouched: not "re-enqueued", not attempt-bumped, not marked DEAD
        for row, expected in ((done, "DONE"), (audit, "PENDING"), (orphan, "PENDING")):
            fresh = s.get(TaskOutbox, row.id)
            assert (fresh.status, fresh.attempt_count) == (expected, 0)


def test_audit_rows_do_not_consume_the_recovery_budget(db, monkeypatch) -> None:
    """Oldest-first + max_rows used to let never-completing audit rows starve
    real stalled jobs queued behind them."""
    stub = MagicMock()
    monkeypatch.setattr(dispatcher, "_dispatch", stub)
    with db() as s:
        for i in range(5):
            _add_row(s, "ingest_complete", {"source_id": f"s{i}"}, age=timedelta(hours=5))
        real = _add_row(s, "research_deep", {"q": "real"}, age=timedelta(hours=1))
        assert recover_stalled(s, cutoff_minutes=30, max_rows=2) == 1
        s.commit()
        stub.assert_called_once_with("research_deep", real.payload)


def test_ancient_unfinished_rows_expire_instead_of_replaying(db, monkeypatch) -> None:
    """Legacy rows written before the completion mark existed must not cause a
    replay storm of long-finished jobs on the first restart after deploy."""
    stub = MagicMock()
    monkeypatch.setattr(dispatcher, "_dispatch", stub)
    with db() as s:
        old = _add_row(s, "research_recommend", {"k": "old"}, age=timedelta(days=30))
        recent = _add_row(s, "research_recommend", {"k": "recent"}, age=timedelta(hours=2))
        assert recover_stalled(s, cutoff_minutes=30) == 1
        s.commit()
        stub.assert_called_once_with("research_recommend", recent.payload)
        assert s.get(TaskOutbox, old.id).status == "DEAD"


# ── end to end: submit → task runs → row DONE; crash → row recoverable ──────


def _eager_celery(monkeypatch) -> None:
    from app.worker.celery_app import celery_app

    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_eager_propagates", False)


def test_task_success_marks_the_outbox_row_done(db, monkeypatch) -> None:
    from app.api._idempotency import enqueue_outbox
    from app.services import doe_cycle_service
    from app.worker.tasks import run_doe_cycle_task

    _eager_celery(monkeypatch)
    monkeypatch.setattr(
        doe_cycle_service, "run_doe_cycle", lambda requirement, budget_remaining=None: {"ok": True}
    )
    payload = {
        "requirement": {
            "product_name": "e2e",
            "domain": "anticorrosion_coating",
            "objectives": [{"metric": "adhesion", "direction": "maximize", "weight": 1.0}],
        },
        "workbench_campaign_id": None,
        "budget_remaining": 4,
    }
    outbox_id = enqueue_outbox("doe_cycle", payload)
    assert outbox_id
    with db() as s:
        assert s.get(TaskOutbox, outbox_id).status == "PENDING"

    res = run_doe_cycle_task.apply(args=(payload,))
    assert res.successful(), res.result

    with db() as s:
        assert s.get(TaskOutbox, outbox_id).status == "DONE"
        # a finished job is never recovered
        assert recover_stalled(s, cutoff_minutes=1) == 0


def test_task_terminal_failure_also_marks_done(db, monkeypatch) -> None:
    """A contract failure is final (recorded in the task snapshot) — replaying
    it on every restart would just fail again."""
    from app.api._idempotency import enqueue_outbox
    from app.worker.tasks import run_doe_cycle_task

    _eager_celery(monkeypatch)
    payload = {"requirement": {}, "workbench_campaign_id": None, "budget_remaining": 1}
    outbox_id = enqueue_outbox("doe_cycle", payload)
    assert outbox_id

    res = run_doe_cycle_task.apply(args=(payload,))
    assert res.failed()

    with db() as s:
        assert s.get(TaskOutbox, outbox_id).status == "DONE"


def test_interrupted_task_stays_recoverable(db, monkeypatch) -> None:
    """No terminal hook ran (worker killed mid-task) → row stays PENDING →
    the next startup replays it, once."""
    from app.api._idempotency import enqueue_outbox
    from app.worker import tasks as worker_tasks

    payload = {"requirement": {"product_name": "x"}, "workbench_campaign_id": 1, "budget_remaining": 2}
    outbox_id = enqueue_outbox("doe_cycle", payload)
    assert outbox_id
    with db() as s:
        # Nothing has touched the row for an hour (no heartbeat either).
        row = s.get(TaskOutbox, outbox_id)
        row.created_at = _naive_now() - timedelta(hours=1)
        row.updated_at = _naive_now() - timedelta(hours=1)
        s.commit()

    sent = MagicMock()
    monkeypatch.setattr(worker_tasks.run_doe_cycle_task, "delay", sent)
    with db() as s:
        assert recover_stalled(s, cutoff_minutes=30) == 1
        s.commit()
    sent.assert_called_once_with(payload)


# ── heartbeat: a job that runs longer than the cutoff is not replayed underneath itself ─


def _row_times(db, row_id: str) -> tuple[datetime, str]:
    with db() as s:
        row = s.get(TaskOutbox, row_id)
        return row.updated_at, row.status


def test_a_touched_row_is_not_stalled_even_though_it_was_created_long_ago(db, monkeypatch) -> None:
    """Recovery keyed on ``created_at``: a loop or a whole-library KB ingest that simply
    takes longer than the 30-minute cutoff was queued again while still running."""
    stub = MagicMock()
    monkeypatch.setattr(dispatcher, "_dispatch", stub)
    payload = {"q": "long running"}
    with db() as s:
        row = _add_row(s, "research_deep", payload, age=timedelta(hours=3))
        row_id = row.id
        assert recover_stalled(s, cutoff_minutes=30, max_rows=5) == 1  # untouched → replayed
        s.commit()
    stub.reset_mock()

    with db() as s:
        row = s.get(TaskOutbox, row_id)
        row.status, row.attempt_count = "PENDING", 0
        row.updated_at = _naive_now() - timedelta(hours=3)
        s.commit()
    assert touch("research_deep", payload) is True  # the running worker's heartbeat

    with db() as s:
        assert recover_stalled(s, cutoff_minutes=30) == 0
        s.commit()
    stub.assert_not_called()
    assert _row_times(db, row_id)[0] > _naive_now() - timedelta(minutes=1)


def test_touch_leaves_finished_and_unknown_rows_alone(db) -> None:
    with db() as s:
        done = _add_row(s, "research_deep", {"k": "done"}, age=timedelta(hours=2), status="DONE")
        done_id = done.id
    before = _row_times(db, done_id)[0]
    assert touch("research_deep", {"k": "done"}) is False
    assert _row_times(db, done_id)[0] == before
    assert touch("research_deep", {"k": "never enqueued"}) is False


def test_heartbeat_beats_immediately_then_periodically_and_stops_with_the_block(db) -> None:
    import time

    payload = {"k": "beat"}
    with db() as s:
        row = _add_row(s, "doe_cycle", payload, age=timedelta(hours=2))
        row_id = row.id
    old = _row_times(db, row_id)[0]

    with heartbeat("doe_cycle", payload, interval_s=0.05):
        deadline = time.monotonic() + 3
        first = old
        while time.monotonic() < deadline:
            first = _row_times(db, row_id)[0]
            if first > old:
                break
            time.sleep(0.02)
        assert first > old, "no immediate beat"
        deadline = time.monotonic() + 3
        later = first
        while time.monotonic() < deadline:
            later = _row_times(db, row_id)[0]
            if later > first:
                break
            time.sleep(0.02)
        assert later > first, "no periodic beat"

    settled = _row_times(db, row_id)[0]
    time.sleep(0.25)
    assert _row_times(db, row_id)[0] == settled, "heartbeat outlived its block"


def test_heartbeat_is_a_no_op_without_an_operation_or_a_dict_payload() -> None:
    with heartbeat("", {"a": 1}):
        pass
    with heartbeat("doe_cycle", "not a dict"):
        pass


def test_the_task_body_runs_under_the_heartbeat(db, monkeypatch) -> None:
    """The base class wraps __call__, which both a worker and eager ``apply`` go through."""
    import time

    from app.api._idempotency import enqueue_outbox
    from app.services import doe_cycle_service
    from app.worker.tasks import run_doe_cycle_task

    _eager_celery(monkeypatch)
    payload = {
        "requirement": {
            "product_name": "hb",
            "domain": "anticorrosion_coating",
            "objectives": [{"metric": "adhesion", "direction": "maximize", "weight": 1.0}],
        },
        "workbench_campaign_id": None,
        "budget_remaining": 4,
    }
    outbox_id = enqueue_outbox("doe_cycle", payload)
    with db() as s:
        row = s.get(TaskOutbox, outbox_id)
        row.updated_at = _naive_now() - timedelta(hours=2)
        s.commit()

    seen: dict[str, datetime] = {}

    def body(requirement, budget_remaining=None):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            seen["updated_at"] = _row_times(db, outbox_id)[0]
            if seen["updated_at"] > _naive_now() - timedelta(minutes=1):
                break
            time.sleep(0.02)
        return {"ok": True}

    monkeypatch.setattr(doe_cycle_service, "run_doe_cycle", body)
    started = _naive_now()
    res = run_doe_cycle_task.apply(args=(payload,))
    assert res.successful(), res.result
    # Compared with when the task *started*, not with "now − 1 min": the task's own epilogue (progress publishing)
    # can take tens of seconds on a machine where Redis is down and each attempt is slow (Windows), which made a
    # heartbeat that did happen look stale by the time it was checked.
    assert seen["updated_at"] >= started, "no heartbeat while the body ran"


def test_ancient_rows_with_a_recent_heartbeat_are_not_expired(db, monkeypatch) -> None:
    stub = MagicMock()
    monkeypatch.setattr(dispatcher, "_dispatch", stub)
    with db() as s:
        row = _add_row(s, "research_recommend", {"k": "slow"}, age=timedelta(days=3))
        row_id = row.id
    assert touch("research_recommend", {"k": "slow"}) is True
    with db() as s:
        assert recover_stalled(s, cutoff_minutes=30) == 0
        s.commit()
        assert s.get(TaskOutbox, row_id).status == "PENDING"  # beating, not DEAD
    stub.assert_not_called()


# ── replay under the client's Celery id ─────────────────────────────────────


def test_replay_runs_under_the_original_celery_id(db, monkeypatch) -> None:
    """The client polls the id it was given; a replay under a new one finished where
    nobody looked."""
    from app.worker import tasks as worker_tasks

    delay = MagicMock()
    apply_async = MagicMock()
    monkeypatch.setattr(worker_tasks.run_doe_cycle_task, "delay", delay)
    monkeypatch.setattr(worker_tasks.run_doe_cycle_task, "apply_async", apply_async)
    payload = {"requirement": {"product_name": "x"}, "workbench_campaign_id": 3, "budget_remaining": 5}
    with db() as s:
        row = _add_row(s, "doe_cycle", payload)
        row.task_id = "client-task-123"
        row.updated_at = _naive_now() - timedelta(hours=1)  # an explicit value beats onupdate
        s.commit()
        assert recover_stalled(s, cutoff_minutes=30) == 1
        s.commit()
    apply_async.assert_called_once_with(args=(payload,), task_id="client-task-123")
    delay.assert_not_called()


def test_rows_without_a_recorded_id_are_replayed_as_before(db, monkeypatch) -> None:
    from app.worker import tasks as worker_tasks

    delay = MagicMock()
    apply_async = MagicMock()
    monkeypatch.setattr(worker_tasks.run_doe_cycle_task, "delay", delay)
    monkeypatch.setattr(worker_tasks.run_doe_cycle_task, "apply_async", apply_async)
    payload = {"requirement": {"product_name": "legacy"}, "workbench_campaign_id": 1, "budget_remaining": 1}
    with db() as s:
        _add_row(s, "doe_cycle", payload)
        assert recover_stalled(s, cutoff_minutes=30) == 1
        s.commit()
    delay.assert_called_once_with(payload)
    apply_async.assert_not_called()


def test_resubmitting_a_finished_job_forgets_the_old_task_id(db) -> None:
    payload = {"k": "again"}
    with db() as s:
        row = _add_row(s, "research_deep", payload, status="DONE")
        row.task_id = "old-id"
        row.updated_at = _naive_now() - timedelta(hours=1)
        s.commit()
        enqueue(s, "research_deep", idempotency_key("research_deep", payload), payload)
        s.commit()
        assert s.get(TaskOutbox, row.id).task_id is None


def test_record_task_id_is_best_effort(db, monkeypatch) -> None:
    with db() as s:
        row = _add_row(s, "research_deep", {"k": "id"})
        row_id = row.id
    assert record_task_id(row_id, "abc") is True
    with db() as s:
        assert s.get(TaskOutbox, row_id).task_id == "abc"
    assert record_task_id(None, "abc") is False
    assert record_task_id(row_id, "") is False
    monkeypatch.setenv("FORMUMIND_DB_URL", "sqlite:////nonexistent-dir/x/y.db")
    assert record_task_id(row_id, "never raises") in (True, False)


def test_submit_records_the_celery_id_it_hands_out_eager(db, monkeypatch) -> None:
    import json
    import time

    from app.api._dispatch import submit
    from app.config import get_settings

    monkeypatch.setenv("FORMUMIND_CELERY_EAGER", "true")
    get_settings.cache_clear()
    ran = []

    class _Task:
        def apply(self, args=None, task_id=None, **_):
            ran.append(task_id)

    payload = {"k": "eager-submit"}
    with db() as s:
        row = _add_row(s, "doe_cycle", payload, age=timedelta(seconds=1))
        row_id = row.id
    response = submit(_Task(), payload, "doe_cycle", outbox_id=row_id)
    task_id = json.loads(response.body)["task_id"]
    with db() as s:
        assert s.get(TaskOutbox, row_id).task_id == task_id
    deadline = time.monotonic() + 3
    while not ran and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ran == [task_id]


def test_submit_records_the_celery_id_it_hands_out_via_the_broker(db, monkeypatch) -> None:
    import json
    from types import SimpleNamespace

    from app.api import _dispatch
    from app.config import get_settings

    monkeypatch.setenv("FORMUMIND_CELERY_EAGER", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(_dispatch, "broker_reachable", lambda: True)
    monkeypatch.setattr(_dispatch, "_delay_with_timeout", lambda task, payload: SimpleNamespace(id="broker-id-9"))
    payload = {"k": "broker-submit"}
    with db() as s:
        row = _add_row(s, "doe_cycle", payload, age=timedelta(seconds=1))
        row_id = row.id
    response = _dispatch.submit(object(), payload, "doe_cycle", outbox_id=row_id)
    assert json.loads(response.body)["task_id"] == "broker-id-9"
    with db() as s:
        assert s.get(TaskOutbox, row_id).task_id == "broker-id-9"

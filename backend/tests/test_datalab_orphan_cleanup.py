"""``datalab_orphan_cleanup`` rows now have a consumer.

A failed saga rollback (Datalab flaky exactly while compensating) records the id of the
sample it could not delete as an outbox row. Nothing read those rows, so the ELN kept
samples no experiment referenced, forever.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db import dispatcher
from app.db.database import make_engine, make_session_factory
from app.db.datalab_client import DatalabStoreError, delete_sample_sync
from app.db.dispatcher import MAX_ATTEMPTS, drain_orphans, list_orphans
from app.db.models import TaskOutbox
from tests.alembic_helpers import run_upgrade


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path}/orphans.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUMIND_DB_URL", db_url)
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


def _orphan(session, item_id: str, *, kind: str = "campaign", status: str = "PENDING", attempts: int = 0):
    row = TaskOutbox(
        id=str(uuid.uuid4()),
        operation="datalab_orphan_cleanup",
        idempotency_key=f"orphan:{item_id}",
        payload={"item_id": item_id, "kind": kind},
        status=status,
        attempt_count=attempts,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1),
    )
    session.add(row)
    session.commit()
    return row


def test_pending_orphans_are_deleted_and_marked_done(db) -> None:
    deleted: list[str] = []
    with db() as s:
        a = _orphan(s, "sample-a")
        b = _orphan(s, "sample-b", kind="training")
        summary = drain_orphans(s, deleter=deleted.append)
        assert summary["cleaned"] == 2 and summary["failed"] == 0 and summary["skipped"] == ""
        assert sorted(deleted) == ["sample-a", "sample-b"]
        assert s.get(TaskOutbox, a.id).status == "DONE"
        assert s.get(TaskOutbox, b.id).status == "DONE"
        # nothing left to do on the next pass
        assert drain_orphans(s, deleter=deleted.append)["examined"] == 0


def test_a_failed_delete_stays_pending_and_counts_an_attempt(db) -> None:
    def flaky(item_id: str) -> None:
        raise RuntimeError("datalab 500")

    with db() as s:
        row = _orphan(s, "sample-x")
        summary = drain_orphans(s, deleter=flaky)
        assert summary["failed"] == 1 and summary["cleaned"] == 0 and summary["dead"] == 0
        fresh = s.get(TaskOutbox, row.id)
        assert fresh.status == "PENDING" and fresh.attempt_count == 1


def test_giving_up_after_max_attempts_marks_dead_and_names_the_sample(db, caplog) -> None:
    def always_fails(item_id: str) -> None:
        raise RuntimeError("still down")

    with db() as s:
        row = _orphan(s, "sample-doomed", attempts=MAX_ATTEMPTS - 1)
        with caplog.at_level("ERROR", logger="app.db.dispatcher"):
            summary = drain_orphans(s, deleter=always_fails)
        assert summary["dead"] == 1
        assert s.get(TaskOutbox, row.id).status == "DEAD"
    assert any("sample-doomed" in r.getMessage() and "by hand" in r.getMessage() for r in caplog.records)


def test_one_failure_does_not_stop_the_rest(db) -> None:
    seen: list[str] = []

    def selective(item_id: str) -> None:
        seen.append(item_id)
        if item_id == "bad":
            raise RuntimeError("nope")

    with db() as s:
        _orphan(s, "bad")
        _orphan(s, "good")
        summary = drain_orphans(s, deleter=selective)
        assert (summary["cleaned"], summary["failed"]) == (1, 1)
        assert sorted(seen) == ["bad", "good"]


def test_a_row_without_an_item_id_is_dead_not_retried_forever(db) -> None:
    with db() as s:
        row = TaskOutbox(
            id=str(uuid.uuid4()), operation="datalab_orphan_cleanup", idempotency_key="orphan:",
            payload={}, status="PENDING",
        )
        s.add(row)
        s.commit()
        assert drain_orphans(s, deleter=lambda _i: None)["dead"] == 1
        assert s.get(TaskOutbox, row.id).status == "DEAD"


def test_other_operations_and_finished_rows_are_left_alone(db) -> None:
    called: list[str] = []
    with db() as s:
        other = TaskOutbox(
            id=str(uuid.uuid4()), operation="research_deep", idempotency_key="k", payload={"item_id": "x"},
        )
        s.add(other)
        s.commit()
        _orphan(s, "already-done", status="DONE")
        assert drain_orphans(s, deleter=called.append)["examined"] == 0
        assert called == []
        assert s.get(TaskOutbox, other.id).status == "PENDING"


def test_an_outage_burns_no_attempts(db, monkeypatch) -> None:
    """While Datalab is down the retries must not be used up."""
    monkeypatch.setenv("FORMUMIND_DATALAB_API_URL", "http://datalab.invalid:5001")
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.db.datalab_client.check_datalab_reachable", lambda url, timeout=2.0: (False, "connection refused")
    )
    with db() as s:
        row = _orphan(s, "sample-q")
        summary = drain_orphans(s)
        assert summary["skipped"].startswith("datalab unreachable")
        fresh = s.get(TaskOutbox, row.id)
        assert fresh.status == "PENDING" and fresh.attempt_count == 0


def test_without_a_datalab_url_nothing_is_attempted(db, monkeypatch) -> None:
    monkeypatch.setenv("FORMUMIND_DATALAB_API_URL", "")
    from app.config import get_settings

    get_settings.cache_clear()
    with db() as s:
        _orphan(s, "sample-r")
        assert "not configured" in drain_orphans(s)["skipped"]


def test_list_orphans_reports_counts_and_items(db) -> None:
    with db() as s:
        _orphan(s, "p1")
        _orphan(s, "d1", status="DONE")
        _orphan(s, "x1", status="DEAD", attempts=5)
        out = list_orphans(s)
        assert out["counts"] == {"PENDING": 1, "DONE": 1, "DEAD": 1}
        assert {i["item_id"] for i in out["items"]} == {"p1", "d1", "x1"}
        assert [i["item_id"] for i in list_orphans(s, status="PENDING")["items"]] == ["p1"]


# ── the HTTP call itself ────────────────────────────────────────────────────


_REAL_CLIENT = httpx.Client  # captured once: patching twice must not stack the wrappers


def _patch_client(monkeypatch, handler):
    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _REAL_CLIENT(*args, **kwargs)

    monkeypatch.setattr("httpx.Client", factory)


def test_delete_sample_sync_posts_the_item_id(monkeypatch) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"status": "success"})

    _patch_client(monkeypatch, handler)
    delete_sample_sync("http://datalab.test:5001/", "abc-1")
    assert seen["url"] == "http://datalab.test:5001/delete-sample/"
    assert '"item_id":"abc-1"' in seen["body"].replace(" ", "")


def test_delete_sample_sync_treats_404_as_already_gone(monkeypatch) -> None:
    _patch_client(monkeypatch, lambda request: httpx.Response(404, json={"detail": "no such item"}))
    delete_sample_sync("http://datalab.test:5001", "gone")  # does not raise


def test_delete_sample_sync_raises_on_server_error_and_on_a_failure_status(monkeypatch) -> None:
    _patch_client(monkeypatch, lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(httpx.HTTPStatusError):
        delete_sample_sync("http://datalab.test:5001", "x")
    _patch_client(monkeypatch, lambda request: httpx.Response(200, json={"status": "error"}))
    with pytest.raises(DatalabStoreError):
        delete_sample_sync("http://datalab.test:5001", "x")


# ── ops endpoints and the startup pass ──────────────────────────────────────


def test_ops_endpoints_list_and_clean_up(db, monkeypatch) -> None:
    from app.main import app

    monkeypatch.setenv("FORMUMIND_DATALAB_API_URL", "http://datalab.test:5001")
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.db.datalab_client.check_datalab_reachable", lambda url, timeout=2.0: (True, None)
    )
    deleted: list[str] = []
    monkeypatch.setattr(
        "app.db.datalab_client.delete_sample_sync", lambda url, item_id, **kw: deleted.append(item_id)
    )
    with db() as s:
        _orphan(s, "via-api")

    client = TestClient(app)
    listing = client.get("/api/ops/datalab-orphans").json()
    assert listing["counts"] == {"PENDING": 1}
    assert listing["items"][0]["item_id"] == "via-api"

    result = client.post("/api/ops/datalab-orphans/cleanup").json()
    assert result["cleaned"] == 1 and deleted == ["via-api"]
    assert client.get("/api/ops/datalab-orphans", params={"status": "PENDING"}).json()["items"] == []


def test_startup_recovery_also_drains_orphans(db, monkeypatch) -> None:
    called = {}

    monkeypatch.setattr(dispatcher, "recover_stalled_for_startup", lambda session: 0)
    monkeypatch.setattr(
        dispatcher, "drain_orphans", lambda session, **kw: called.setdefault("drained", True) or {}
    )
    dispatcher.schedule_recover_stalled().join(timeout=10)
    assert called.get("drained") is True

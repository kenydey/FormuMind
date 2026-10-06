"""The closed loop's pause flag lives in the database, lapses visibly, and no longer needs Redis (round-4).

It was a Redis key with a 24 h TTL. Without Redis (development, eager mode) pausing answered 503; with Redis
unreachable the flag read as "not paused" - the user's pause lost exactly when something was wrong; and a pause set
on Friday silently lapsed on Saturday. Now (``db.doe_pause_store``): one row per campaign, a TTL that defaults to
24 h (``FORMUMIND_DOE_CYCLE_PAUSE_TTL_HOURS``, 0 = until resumed), and a lapse that is logged once and reported.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.config import get_settings
from app.db import doe_pause_store as store
from app.db.campaign_store import reset_campaign_store
from app.db.database import Base, default_session_factory, make_engine, make_session_factory
from app.db.models import Campaign, DOECyclePauseRow
from app.main import app

T0 = datetime(2026, 10, 2, 9, 0, 0)  # a Friday morning


# ── the store ────────────────────────────────────────────────────────────────


@pytest.fixture()
def factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/pause.db")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


def _campaign(factory, owner: str | None = None) -> int:
    with factory() as session:
        row = Campaign(name="c", owner_id=owner)
        session.add(row)
        session.commit()
        return row.id


def test_a_campaign_that_was_never_paused_is_running(factory):
    cid = _campaign(factory)
    assert store.read_state(cid, session_factory=factory, now=T0) == store.RUNNING


def test_a_pause_holds_for_its_ttl_and_not_a_minute_longer(factory):
    cid = _campaign(factory)
    state = store.set_paused(cid, ttl_hours=24, session_factory=factory, now=T0)
    assert state.paused and state.paused_until == T0 + timedelta(hours=24)

    just_before = T0 + timedelta(hours=24) - timedelta(seconds=1)
    assert store.read_state(cid, session_factory=factory, now=just_before).paused
    at_expiry = T0 + timedelta(hours=24)
    assert not store.read_state(cid, session_factory=factory, now=at_expiry).paused


def test_a_zero_ttl_is_until_somebody_resumes(factory):
    cid = _campaign(factory)
    for ttl in (0, None):
        store.set_paused(cid, ttl_hours=ttl, session_factory=factory, now=T0)
        state = store.read_state(cid, session_factory=factory, now=T0 + timedelta(days=3650))
        assert state.paused and state.paused_until is None, ttl


def test_it_survives_a_restart(tmp_path):
    """Another engine on the same file - what a restarted API process or a Celery worker is - sees the pause."""
    url = f"sqlite:///{tmp_path}/shared.db"
    first = make_engine(url)
    Base.metadata.create_all(first)
    cid = _campaign(make_session_factory(first))
    store.set_paused(cid, ttl_hours=0, session_factory=make_session_factory(first), now=T0)
    first.dispose()

    second = make_engine(url)
    try:
        assert store.read_state(cid, session_factory=make_session_factory(second), now=T0 + timedelta(days=30)).paused
    finally:
        second.dispose()


def test_a_pause_that_ran_out_is_recorded_once_and_logged_once(factory, caplog):
    cid = _campaign(factory)
    store.set_paused(cid, ttl_hours=24, session_factory=factory, now=T0)
    after = T0 + timedelta(hours=30)

    with caplog.at_level(logging.WARNING, logger=store.logger.name):
        states = [store.read_state(cid, session_factory=factory, now=after + timedelta(minutes=i)) for i in range(3)]
    assert [s.paused for s in states] == [False, False, False]
    # the lapse is stamped with when the pause was due to end, not with when somebody happened to look
    assert {s.lapsed_at for s in states} == {T0 + timedelta(hours=24)}
    messages = [r.getMessage() for r in caplog.records if "ran out" in r.getMessage()]
    assert len(messages) == 1, messages
    assert f"campaign {cid}" in messages[0] and "2026-10-03T09:00:00Z" in messages[0]
    assert "FORMUMIND_DOE_CYCLE_PAUSE_TTL_HOURS=0" in messages[0], "the log says how to get a pause that does not lapse"


def test_two_readers_noticing_the_lapse_together_log_it_once(factory, caplog):
    """The conditional UPDATE lets one reader win; the loser neither stamps nor logs."""
    cid = _campaign(factory)
    store.set_paused(cid, ttl_hours=1, session_factory=factory, now=T0)
    later = T0 + timedelta(hours=2)
    with caplog.at_level(logging.WARNING, logger=store.logger.name):
        store._record_lapse(factory, cid, later)
        store._record_lapse(factory, cid, later)
    assert sum("ran out" in r.getMessage() for r in caplog.records) == 1


def test_pausing_again_after_a_lapse_starts_a_fresh_pause(factory):
    cid = _campaign(factory)
    store.set_paused(cid, ttl_hours=1, session_factory=factory, now=T0)
    assert store.read_state(cid, session_factory=factory, now=T0 + timedelta(hours=2)).lapsed_at
    state = store.set_paused(cid, ttl_hours=1, session_factory=factory, now=T0 + timedelta(hours=3))
    assert state.paused and state.lapsed_at is None
    assert store.read_state(cid, session_factory=factory, now=T0 + timedelta(hours=3, minutes=30)).paused


def test_an_explicit_resume_forgets_the_pause_and_the_lapse_note(factory):
    cid = _campaign(factory)
    store.set_paused(cid, ttl_hours=0, session_factory=factory, now=T0)
    assert store.clear_pause(cid, session_factory=factory) is True
    assert store.read_state(cid, session_factory=factory, now=T0) == store.RUNNING
    assert store.clear_pause(cid, session_factory=factory) is False  # nothing left to forget

    store.set_paused(cid, ttl_hours=1, session_factory=factory, now=T0)
    store.read_state(cid, session_factory=factory, now=T0 + timedelta(hours=2))  # lapses
    assert store.clear_pause(cid, session_factory=factory) is True  # the note goes too
    assert store.read_state(cid, session_factory=factory, now=T0 + timedelta(hours=2)) == store.RUNNING


def test_an_unknown_campaign_is_refused_not_given_an_orphan_row(factory):
    with pytest.raises(store.CampaignNotFoundError):
        store.set_paused(999, ttl_hours=1, session_factory=factory)
    with pytest.raises(store.CampaignNotFoundError):
        store.clear_pause(999, session_factory=factory)
    with pytest.raises(store.CampaignNotFoundError):
        store.campaign_owner(999, session_factory=factory)
    with factory() as session:
        assert session.scalars(select(DOECyclePauseRow)).all() == []


def test_deleting_a_campaign_takes_its_pause_with_it(factory):
    """Foreign keys are on for SQLite here: a recycled campaign id must not inherit a stranger's pause."""
    cid = _campaign(factory)
    store.set_paused(cid, ttl_hours=0, session_factory=factory, now=T0)
    with factory() as session:
        session.execute(text("DELETE FROM campaigns WHERE id = :i"), {"i": cid})
        session.commit()
        assert session.scalars(select(DOECyclePauseRow)).all() == []


def test_the_second_of_two_simultaneous_first_pauses_falls_back_to_an_update(factory, monkeypatch):
    cid = _campaign(factory)
    real = store._write_pause
    calls = []

    def racing(session, campaign_id, paused_at, paused_until):
        calls.append(1)
        if len(calls) == 1:
            raise IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed: doe_cycle_pauses.campaign_id"))
        return real(session, campaign_id, paused_at, paused_until)

    monkeypatch.setattr(store, "_write_pause", racing)
    assert store.set_paused(cid, ttl_hours=1, session_factory=factory, now=T0).paused
    assert len(calls) == 2
    assert store.read_state(cid, session_factory=factory, now=T0).paused


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), store.MAX_TTL_HOURS + 1])
def test_an_absurd_ttl_is_refused(factory, bad):
    cid = _campaign(factory)
    with pytest.raises(ValueError):
        store.set_paused(cid, ttl_hours=bad, session_factory=factory)


def test_times_leave_as_unambiguous_utc():
    """A bare ISO string is read as local time by a browser: the pause would end hours early or late."""
    assert store.utc_iso(datetime(2026, 10, 3, 9, 0, 0, 123456)) == "2026-10-03T09:00:00Z"
    assert store.utc_iso(None) is None


def test_the_setting_and_the_store_agree_on_the_longest_pause():
    from app.config import Settings

    field = Settings.model_fields["doe_cycle_pause_ttl_hours"]
    bounds = {type(m).__name__: getattr(m, "le", None) or getattr(m, "ge", None) for m in field.metadata}
    assert bounds["Le"] == store.MAX_TTL_HOURS
    assert Settings().doe_cycle_pause_ttl_hours == 24.0


# ── the service functions and the endpoints, on the application's database ───


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{tmp_path}/app.db")
    monkeypatch.delenv("FORMUMIND_DOE_CYCLE_PAUSE_TTL_HOURS", raising=False)
    get_settings.cache_clear()
    schema = make_engine(f"sqlite:///{tmp_path}/app.db")
    Base.metadata.create_all(schema)
    schema.dispose()
    # The campaign store is a process-wide singleton bound to whichever database an earlier test used.
    reset_campaign_store()
    yield default_session_factory()
    reset_campaign_store()
    get_settings.cache_clear()


@pytest.fixture()
def clock(monkeypatch):
    """A controllable "now" for the store and the status payload."""
    now = {"value": T0}
    monkeypatch.setattr(store, "utcnow", lambda: now["value"])
    monkeypatch.setattr("app.clock.utcnow", lambda: now["value"])

    class Clock:
        def advance(self, **kwargs):
            now["value"] += timedelta(**kwargs)

    return Clock()


@pytest.fixture()
def no_redis(monkeypatch):
    """Redis is not there, and nothing may reach for it."""

    def reach(*args, **kwargs):  # pragma: no cover - only runs on a regression
        raise AssertionError("the pause flag must not touch Redis")

    monkeypatch.setattr("app.worker.task_progress._redis_client", reach)


def test_pausing_works_without_redis(env, no_redis):
    from app.services import workbench_loop as loop

    cid = _campaign(env)
    assert loop.pause_resume_doecyle(cid, True) is True
    assert loop.is_doecycle_paused(cid) is True
    assert loop.pause_resume_doecyle(cid, False) is True
    assert loop.is_doecycle_paused(cid) is False


def test_the_default_ttl_is_a_day_and_the_setting_changes_it(env, monkeypatch, clock):
    from app.services import workbench_loop as loop

    cid = _campaign(env)
    loop.pause_resume_doecyle(cid, True)
    assert loop.get_doecyle_status(cid)["pausedUntil"] == "2026-10-03T09:00:00Z"

    monkeypatch.setenv("FORMUMIND_DOE_CYCLE_PAUSE_TTL_HOURS", "2")
    get_settings.cache_clear()
    loop.pause_resume_doecyle(cid, True)
    assert loop.get_doecyle_status(cid)["pausedUntil"] == "2026-10-02T11:00:00Z"

    monkeypatch.setenv("FORMUMIND_DOE_CYCLE_PAUSE_TTL_HOURS", "0")
    get_settings.cache_clear()
    loop.pause_resume_doecyle(cid, True)
    status = loop.get_doecyle_status(cid)
    assert status["isPaused"] and status["pausedUntil"] is None
    clock.advance(days=400)
    assert loop.is_doecycle_paused(cid) is True, "TTL 0 means until somebody resumes"

    loop.pause_resume_doecyle(cid, True, ttl_hours=1)  # an explicit TTL beats the setting
    assert loop.get_doecyle_status(cid)["pausedUntil"] is not None


def test_a_database_error_is_not_taken_for_not_paused(env, monkeypatch):
    """The old flag read False when Redis could not be reached - the loop the user stopped started anyway."""
    from app.services import workbench_loop as loop

    def broken(*args, **kwargs):
        raise OperationalError("SELECT", {}, Exception("database is locked"))

    monkeypatch.setattr(store, "read_state", broken)
    with pytest.raises(OperationalError):
        loop.is_doecycle_paused(1)
    assert loop.get_doecyle_status(1) is None  # the polling endpoint degrades instead (below)


def test_the_status_reports_a_lapse_for_a_week(env, clock):
    from app.services import workbench_loop as loop

    cid = _campaign(env)
    loop.pause_resume_doecyle(cid, True)
    clock.advance(hours=25)
    status = loop.get_doecyle_status(cid)
    assert status["isPaused"] is False and status["pausedUntil"] is None
    assert status["lapsedAt"] == "2026-10-03T09:00:00Z"

    clock.advance(days=6)
    assert loop.get_doecyle_status(cid)["lapsedAt"] == "2026-10-03T09:00:00Z"
    clock.advance(days=2)
    assert loop.get_doecyle_status(cid)["lapsedAt"] is None, "an old lapse is not news"


def test_the_derived_loop_status_says_paused_until_the_pause_lapses(env, clock):
    from app.services import workbench_loop as loop

    cid = _campaign(env)
    loop.pause_resume_doecyle(cid, True, ttl_hours=2)
    paused = loop.campaign_loop_status(cid)
    assert paused["status"] == "paused" and paused["paused"] is True
    assert paused["paused_until"] == "2026-10-02T11:00:00Z"
    assert "自动恢复" in paused["message"]

    loop.pause_resume_doecyle(cid, True, ttl_hours=0)
    assert "恢复前一直暂停" in loop.campaign_loop_status(cid)["message"]

    loop.pause_resume_doecyle(cid, True, ttl_hours=1)
    clock.advance(hours=2)
    after = loop.campaign_loop_status(cid)
    assert after["status"] == "idle" and after["paused"] is False and after["paused_until"] is None


def test_a_paused_campaign_is_not_started_by_the_loop_dispatch(env, no_redis):
    from app.services import workbench_loop as loop

    cid = _campaign(env)
    loop.pause_resume_doecyle(cid, True)
    task_id, message = loop.dispatch_loop_after_sync(
        training_ingested=3, workbench_campaign_id=cid, trigger_loop=True
    )
    assert task_id is None and "已暂停" in message


@pytest.mark.parametrize("which", ["loop", "doe_cycle"])
def test_the_worker_tasks_refuse_a_paused_campaign(env, no_redis, which):
    from app.services import workbench_loop as loop
    from app.worker import tasks

    cid = _campaign(env)
    loop.pause_resume_doecyle(cid, True)
    payload = {"workbench_campaign_id": cid, "requirement": {}}
    if which == "loop":
        result = tasks.run_loop_iterate_impl("t-paused-loop", payload)
    else:
        result = tasks.run_doe_cycle_task.apply(args=[payload]).get()
    assert result["paused"] is True and result["campaign_id"] == cid

    loop.pause_resume_doecyle(cid, False)
    assert loop.is_doecycle_paused(cid) is False


# ── the endpoints ────────────────────────────────────────────────────────────

client = TestClient(app)


def _post(cid, body):
    return client.post(f"/api/experiments/hooks/pause-doecycle/{cid}", json=body)


def _status(cid):
    return client.get(f"/api/experiments/hooks/doecyle-status/{cid}")


def test_pause_status_resume_round_trip(env, no_redis, clock):
    cid = _campaign(env)
    r = _post(cid, {"isPaused": True})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "status": "success",
        "message": f"DOE cycle paused for campaign {cid}",
        "isPaused": True,
        "pausedUntil": "2026-10-03T09:00:00Z",
    }
    body = _status(cid).json()
    assert body["isPaused"] is True and body["pausedUntil"] == "2026-10-03T09:00:00Z"
    assert body["lastUpdated"] == "2026-10-02T09:00:00Z" and body["campaignId"] == cid

    r = _post(cid, {"isPaused": False})
    assert r.status_code == 200 and r.json()["isPaused"] is False and r.json()["pausedUntil"] is None
    assert _status(cid).json()["isPaused"] is False


def test_ttl_hours_comes_with_the_request(env, clock):
    cid = _campaign(env)
    assert _post(cid, {"isPaused": True, "ttlHours": 6}).json()["pausedUntil"] == "2026-10-02T15:00:00Z"
    assert _post(cid, {"isPaused": True, "ttlHours": 0}).json()["pausedUntil"] is None


def test_the_lapse_is_visible_through_the_endpoint(env, clock):
    cid = _campaign(env)
    _post(cid, {"isPaused": True, "ttlHours": 1})
    clock.advance(hours=3)
    body = _status(cid).json()
    assert body["isPaused"] is False and body["lapsedAt"] == "2026-10-02T10:00:00Z"


@pytest.mark.parametrize(
    "body",
    [
        {"isPaused": True, "ttlHours": -1},
        {"isPaused": True, "ttlHours": store.MAX_TTL_HOURS + 1},
        {"isPaused": True, "ttlHours": "soon"},
        {"isPaused": "maybe"},
    ],
)
def test_a_malformed_request_is_a_422(env, body):
    cid = _campaign(env)
    assert _post(cid, body).status_code == 422


def test_an_unknown_campaign_is_a_404_to_pause_and_not_paused_to_ask(env):
    assert _post(4242, {"isPaused": True}).status_code == 404
    assert _post(4242, {"isPaused": False}).status_code == 404
    assert _post(10**30, {"isPaused": True}).status_code == 404  # beyond the database's integers
    r = _status(4242)
    assert r.status_code == 200 and r.json()["isPaused"] is False


def test_an_unwritable_database_is_a_503(env, monkeypatch):
    cid = _campaign(env)

    def locked(*args, **kwargs):
        raise OperationalError("INSERT", {}, Exception("database is locked"))

    monkeypatch.setattr(store, "set_paused", locked)
    r = _post(cid, {"isPaused": True})
    assert r.status_code == 503 and "state store unavailable" in r.json()["detail"]


def test_an_unreadable_database_degrades_the_poll_instead_of_failing_it(env, monkeypatch):
    cid = _campaign(env)

    def broken(*args, **kwargs):
        raise OperationalError("SELECT", {}, Exception("disk I/O error"))

    monkeypatch.setattr(store, "read_state", broken)
    r = _status(cid)
    assert r.status_code == 200
    assert r.json()["degraded"] is True and r.json()["isPaused"] is False


def test_someone_elses_campaign_is_not_yours_to_pause(env, monkeypatch):
    """The endpoints looked at no campaign at all before, so any caller could pause - or resume - any loop."""
    cid = _campaign(env, owner="alice")
    monkeypatch.setattr("app.middleware.api_auth.get_current_owner", lambda request: "bob")
    assert _post(cid, {"isPaused": True}).status_code == 403
    assert _status(cid).status_code == 403
    monkeypatch.setattr("app.middleware.api_auth.get_current_owner", lambda request: "alice")
    assert _post(cid, {"isPaused": True}).status_code == 200
    assert _status(cid).json()["isPaused"] is True


# ── the schema ───────────────────────────────────────────────────────────────


def test_make_engine_creates_the_table_for_a_database_that_predates_it(tmp_path, monkeypatch):
    """A production database that has not been migrated yet must not lose its loop to a missing table."""
    from sqlalchemy import create_engine, inspect

    url = f"sqlite:///{tmp_path}/old.db"
    seed = create_engine(url)
    Base.metadata.create_all(seed)
    with seed.begin() as conn:
        conn.execute(text("DROP TABLE doe_cycle_pauses"))
    assert "doe_cycle_pauses" not in inspect(seed).get_table_names()
    seed.dispose()

    monkeypatch.setenv("FORMUMIND_ENVIRONMENT", "production")  # create_all is skipped in production
    get_settings.cache_clear()
    try:
        engine = make_engine(url)
        assert "doe_cycle_pauses" in inspect(engine).get_table_names()
        engine.dispose()
    finally:
        get_settings.cache_clear()


def test_the_migration_creates_the_table_and_is_repeatable(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, inspect

    from tests.alembic_helpers import run_downgrade, run_upgrade

    url = f"sqlite:///{tmp_path}/mig.db"
    run_upgrade(url, monkeypatch, "head")
    engine = create_engine(url)
    try:
        assert {c["name"] for c in inspect(engine).get_columns("doe_cycle_pauses")} == {
            "campaign_id", "paused_at", "paused_until", "lapsed_at",
        }
        fks = inspect(engine).get_foreign_keys("doe_cycle_pauses")
        assert [(fk["referred_table"], fk["options"].get("ondelete")) for fk in fks] == [("campaigns", "CASCADE")]
    finally:
        engine.dispose()

    run_downgrade(url, monkeypatch, "0044")
    engine = create_engine(url)
    try:
        assert "doe_cycle_pauses" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
    run_upgrade(url, monkeypatch, "head")
    run_upgrade(url, monkeypatch, "head")  # repeatable

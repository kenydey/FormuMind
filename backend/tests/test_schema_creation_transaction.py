"""A fresh SQLite database is built in one transaction, not one commit per CREATE statement (round-5).

pysqlite opens no transaction for DDL, so ``create_all`` on SQLite committed - and fsynced - every one of the ~110
``CREATE TABLE`` / ``CREATE INDEX`` statements separately. On a Windows runner that was 3 to 26 seconds in the *setup* of
each test that builds a database (the ``test_outbox_lifecycle`` fixtures were the slowest tests in a shard, 26 s at worst,
and the shard that holds them ran 7-9 minutes against 4.5 for the others). A crash half-way also left a half-built schema.

The assertion cannot be about seconds - a Linux disk will not show it - so it is about what causes them: every table is
created while a transaction is open, and a failure part-way leaves nothing behind.
"""
from __future__ import annotations

import multiprocessing as mp
import sqlite3
import threading

import pytest
from sqlalchemy import Table, event, inspect

from app.db.database import Base, create_all_metadata, make_engine, make_session_factory
from app.db.models import Campaign

EXPECTED = len(Base.metadata.tables)


def _bare_engine(tmp_path, name="fresh.db"):
    from sqlalchemy import create_engine

    return create_engine(f"sqlite:///{tmp_path}/{name}", future=True)


@pytest.fixture()
def created_in_transaction():
    """Records ``in_transaction`` of the raw sqlite3 connection as each table is created."""
    seen: list[bool] = []

    def record(target, connection, **kw):
        seen.append(bool(connection.connection.dbapi_connection.in_transaction))

    event.listen(Table, "before_create", record)
    yield seen
    event.remove(Table, "before_create", record)


def test_every_table_is_created_inside_one_open_transaction(tmp_path, created_in_transaction):
    engine = _bare_engine(tmp_path)
    create_all_metadata(engine)
    assert len(created_in_transaction) == EXPECTED >= 25, "not vacuous: the whole schema was created while listening"
    assert all(created_in_transaction), "a CREATE ran outside a transaction: it commits - and fsyncs - on its own"
    assert set(inspect(engine).get_table_names()) >= {"campaigns", "task_outbox", "doe_cycle_pauses"}
    engine.dispose()


def test_make_engine_builds_the_schema_the_same_way(tmp_path, created_in_transaction):
    engine = make_engine(f"sqlite:///{tmp_path}/via_make_engine.db")
    assert len(created_in_transaction) == EXPECTED and all(created_in_transaction)
    engine.dispose()


def test_a_failure_half_way_leaves_no_half_built_schema(tmp_path):
    engine = _bare_engine(tmp_path, "interrupted.db")
    created: list[str] = []

    def die_on_the_fifth(target, connection, **kw):
        created.append(target.name)
        if len(created) == 5:
            raise RuntimeError("disk full")

    event.listen(Table, "after_create", die_on_the_fifth)
    try:
        with pytest.raises(RuntimeError, match="disk full"):
            create_all_metadata(engine)
    finally:
        event.remove(Table, "after_create", die_on_the_fifth)
    assert len(created) == 5
    assert inspect(engine).get_table_names() == [], "the four tables created before the failure must have been rolled back"

    create_all_metadata(engine)  # and the retry is not confused by what the failed run left
    assert len(inspect(engine).get_table_names()) == EXPECTED
    engine.dispose()


def test_running_it_again_is_a_no_op_that_keeps_the_data(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/again.db")
    factory = make_session_factory(engine)
    with factory() as session:
        session.add(Campaign(name="kept"))
        session.commit()
    create_all_metadata(engine)
    create_all_metadata(engine)
    with factory() as session:
        assert [c.name for c in session.query(Campaign).all()] == ["kept"]
    engine.dispose()


def test_a_connection_passed_in_keeps_the_callers_transaction_rules(tmp_path):
    """Alembic's baseline hands over its own connection; that path is not ours to wrap."""
    engine = _bare_engine(tmp_path, "alembic_like.db")
    with engine.connect() as conn:
        create_all_metadata(conn)
        conn.commit()
    assert len(inspect(engine).get_table_names()) == EXPECTED
    engine.dispose()


# ── the first boot: several processes, one empty file ────────────────────────────


def _boot(db_url: str, barrier, queue) -> None:
    """What the API, the worker and the scheduler each do at start: build the engine, and with it the schema."""
    import logging
    import os

    os.environ["FORMUMIND_API_AUTH_ENABLED"] = "false"
    logging.disable(logging.CRITICAL)
    from sqlalchemy import inspect as _inspect

    from app.db.database import make_engine as _make

    barrier.wait(timeout=120)
    try:
        engine = _make(db_url)
        queue.put(("ok", len(_inspect(engine).get_table_names())))
        engine.dispose()
    except Exception as exc:  # noqa: BLE001 - the message is the assertion
        queue.put(("error", f"{type(exc).__name__}: {str(exc)[:160]}"))


def test_processes_booting_together_against_an_empty_file_all_succeed(tmp_path):
    """Measured on the previous code with four processes: 24 of 32 attempts failed (``table experiments already
    exists`` with a commit per statement, ``database is locked`` with a plain BEGIN). Spawned, so it runs on Windows too."""
    ctx = mp.get_context("spawn")
    for trial in range(2):
        url = f"sqlite:///{tmp_path}/boot{trial}.db"
        barrier, queue = ctx.Barrier(4), ctx.Queue()
        procs = [ctx.Process(target=_boot, args=(url, barrier, queue)) for _ in range(4)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=240)
        results = [queue.get(timeout=10) for _ in procs]
        assert all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs]
        assert results == [("ok", EXPECTED)] * 4, results


def test_a_complete_schema_does_not_queue_behind_a_writer(tmp_path):
    """Every boot after the first finds nothing to create; it must not wait for a long write transaction elsewhere
    just to find that out (``busy_timeout`` is 60 s)."""
    engine = make_engine(f"sqlite:///{tmp_path}/busy.db")
    other = sqlite3.connect(tmp_path / "busy.db", isolation_level=None)
    other.execute("BEGIN IMMEDIATE")  # a writer holds the lock for the whole test
    try:
        worker = threading.Thread(target=create_all_metadata, args=(engine,), daemon=True)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive(), "create_all_metadata waited for the writer although the schema was complete"
    finally:
        other.execute("ROLLBACK")
        other.close()
        engine.dispose()

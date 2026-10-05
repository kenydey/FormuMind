"""``app.clock.utcnow`` never repeats a value inside a process (a Windows-clock bug, reproduced on any OS).

Windows' wall clock advances in ~15.6 ms steps. Two rows created inside one step got the same ``created_at``, and
every ``ORDER BY created_at DESC`` ("newest first") put them in arbitrary order — in CI that failed three tests
that save two things back to back and ask for the newest. Freezing the OS clock reproduces the tie on Linux.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta

import pytest

from app import clock


@pytest.fixture()
def frozen(monkeypatch):
    now = datetime(2026, 10, 5, 12, 0, 0)
    state = {"now": now}
    monkeypatch.setattr(clock, "_now", lambda: state["now"])
    monkeypatch.setattr(clock, "_last", None)
    return state


def test_a_frozen_os_clock_still_yields_strictly_increasing_values(frozen):
    values = [clock.utcnow() for _ in range(50)]
    assert values == sorted(values) and len(set(values)) == 50
    assert values[0] == frozen["now"]


def test_values_follow_the_clock_once_it_moves_on(frozen):
    first = clock.utcnow()
    clock.utcnow()
    frozen["now"] += timedelta(milliseconds=16)
    assert clock.utcnow() == first + timedelta(milliseconds=16)


def test_a_clock_stepped_back_by_more_than_the_skew_is_taken_at_its_word(frozen):
    clock.utcnow()
    frozen["now"] -= timedelta(hours=1)  # NTP step / manual change: not a coarse clock
    assert clock.utcnow() == frozen["now"]


def test_a_small_regression_does_not_run_time_backwards(frozen):
    first = clock.utcnow()
    frozen["now"] -= timedelta(milliseconds=5)
    assert clock.utcnow() > first


def test_threads_never_share_a_timestamp(frozen):
    seen: list[datetime] = []
    guard = threading.Lock()

    def work():
        mine = [clock.utcnow() for _ in range(200)]
        with guard:
            seen.extend(mine)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(seen) == len(set(seen)) == 1600


def test_the_database_defaults_use_this_clock():
    """41 ``created_at`` / ``updated_at`` columns default to ``models._utcnow``; it must be this one."""
    from app.db import models

    assert models._utcnow is clock.utcnow


def test_newest_adopted_recommendation_is_first_even_when_the_clock_does_not_move(frozen, tmp_path, monkeypatch):
    from app.db import recommend_outcome_store as store
    from app.db.database import make_engine, make_session_factory

    engine = make_engine(f"sqlite:///{tmp_path}/clock.db")
    try:
        with make_session_factory(engine)() as session:
            for i in range(3):
                store.record_adopt(session, recommend_id=f"rr-{i}")
            assert [r["recommend_id"] for r in store.recent_adopted(session, limit=2)] == ["rr-2", "rr-1"]
    finally:
        engine.dispose()

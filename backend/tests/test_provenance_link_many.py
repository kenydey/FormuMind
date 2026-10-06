"""Provenance edges are written in batches, under the shared write lock, and reads write nothing (round-5).

``provenance.link`` opened its own session, ran ``ensure_provenance`` (a ``CREATE ... IF NOT EXISTS`` pass and a ``commit``)
and then a second session for the insert with a bare ``commit`` - so it never went through ``commit_session``'s
cross-process write lock and "database is locked" retry, and a DOE cycle of N runs x 5 candidates was 2 * 5N transactions.
Even ``lineage`` and ``count_edges`` began with a write. Now ``link_many`` is one ``commit_session`` write for the whole batch,
and ``ensure_provenance`` is a read when the schema is already there.
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import sqlite3

import pytest
from sqlalchemy import event

from app.db.database import Base, make_engine, make_session_factory
from app.services import doe_cycle_service, provenance


@pytest.fixture()
def factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/prov.db")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture()
def commits(factory):
    """Counts the commits the engine behind ``factory`` performs."""
    counter = {"n": 0}

    @event.listens_for(factory.kw["bind"], "commit")
    def _count(conn):
        counter["n"] += 1

    return counter


def _runs(n_runs, n_candidates):
    return [("run", f"exp-{r}", "formulation", f"f{c}", "tests") for r in range(n_runs) for c in range(n_candidates)]


def test_a_batch_is_one_transaction(factory, commits):
    provenance.ensure_provenance(factory)
    commits["n"] = 0
    assert provenance.link_many(_runs(20, 5), session_factory=factory) is True
    assert commits["n"] == 1, "100 edges must be one commit, not one per edge"
    assert provenance.count_edges(session_factory=factory) == 100


def test_single_links_are_still_each_their_own_transaction(factory, commits):
    """What the batch replaces: a lone ``link`` is one write now - it was two (the schema pass, then the insert)."""
    provenance.ensure_provenance(factory)
    commits["n"] = 0
    for edge in _runs(4, 5):
        assert provenance.link(*edge, session_factory=factory) is True
    assert commits["n"] == 20


def test_it_is_idempotent_and_overlapping_batches_merge(factory):
    assert provenance.link_many(_runs(3, 2), session_factory=factory)
    assert provenance.link_many(_runs(3, 2), session_factory=factory)
    assert provenance.link_many(_runs(5, 2), session_factory=factory)  # 3 runs seen, 2 new
    assert provenance.count_edges(session_factory=factory) == 10


def test_a_missing_relation_defaults_and_an_empty_batch_is_a_no_op(factory):
    assert provenance.link_many([("run", "a", "formulation", "b")], session_factory=factory)
    assert provenance.link_many([], session_factory=factory) is True
    edges = provenance.lineage("formulation", "b", session_factory=factory)
    assert [(e["from_id"], e["relation"]) for e in edges] == [("a", "derived_from")]


def test_one_bad_edge_rejects_the_whole_batch_before_anything_is_written(factory):
    batch = [("run", "ok", "formulation", "f", "tests"), ("nonsense", "x", "formulation", "f", "tests")]
    with pytest.raises(ValueError, match="unknown provenance node type"):
        provenance.link_many(batch, session_factory=factory)
    with pytest.raises(ValueError, match="non-empty"):
        provenance.link_many([("run", "", "formulation", "f", "tests")], session_factory=factory)
    assert provenance.count_edges(session_factory=factory) == 0


def test_a_database_error_is_fail_open(factory, caplog):
    class Broken:
        def __call__(self):
            raise sqlite3.OperationalError("disk I/O error")

    with caplog.at_level(logging.WARNING, logger=provenance.logger.name):
        assert provenance.link_many(_runs(1, 1), session_factory=Broken()) is False
        assert provenance.link("run", "a", "formulation", "b", session_factory=Broken()) is False
    assert sum("fail-open" in r.getMessage() for r in caplog.records) == 2


def test_link_is_the_one_edge_case_of_link_many(factory, monkeypatch):
    seen = []
    real = provenance.link_many
    monkeypatch.setattr(provenance, "link_many", lambda edges, **kw: seen.append(list(edges)) or real(edges, **kw))
    assert provenance.link("run", "a", "formulation", "b", "tests", session_factory=factory) is True
    assert seen == [[("run", "a", "formulation", "b", "tests")]]


def test_a_claim_with_many_sources_is_one_transaction(factory, commits):
    provenance.ensure_provenance(factory)
    commits["n"] = 0
    claim = provenance.record_claim_sources(
        "涂层耐盐雾 720 小时", ["s1", "s2", "s2", "", "s3"], session_factory=factory
    )
    assert claim == provenance.claim_id_for_text("涂层耐盐雾 720 小时")
    assert commits["n"] == 1
    assert provenance.count_edges(session_factory=factory) == 3, "s2 twice and the empty id are not three more edges"
    for sid in ("s1", "s2", "s3"):
        upstream = provenance.lineage("source", sid, session_factory=factory)
        assert [(e["from_id"], e["relation"]) for e in upstream] == [(claim, "cites")]


def test_an_up_to_date_schema_costs_no_write_to_check(factory, commits):
    provenance.ensure_provenance(factory)
    commits["n"] = 0
    for _ in range(5):
        provenance.ensure_provenance(factory)
    provenance.lineage("source", "s", session_factory=factory)
    provenance.count_edges(session_factory=factory)
    assert commits["n"] == 0, "reads and re-checks must not open a write transaction"


def test_a_stale_schema_is_rebuilt_once(factory, commits):
    provenance.ensure_provenance(factory)
    with factory() as session:
        session.execute(provenance.text("UPDATE provenance_meta SET v = 'old' WHERE k = 'schema'"))
        session.commit()
    provenance.ensure_provenance(factory)
    commits["n"] = 0
    provenance.ensure_provenance(factory)
    assert commits["n"] == 0
    with factory() as session:
        assert session.execute(provenance.text("SELECT v FROM provenance_meta WHERE k = 'schema'")).scalar() == provenance._PROV_SCHEMA


def test_a_dropped_index_is_noticed_and_recreated(factory):
    provenance.ensure_provenance(factory)
    with factory() as session:
        session.execute(provenance.text("DROP INDEX idx_prov_to"))
        session.commit()
    provenance.ensure_provenance(factory)
    with factory() as session:
        names = {r[0] for r in session.execute(provenance.text("SELECT name FROM sqlite_master WHERE type = 'index'"))}
    assert {"idx_prov_from", "idx_prov_to"} <= names


def test_the_doe_cycle_links_all_runs_in_one_batch(factory, commits, monkeypatch):
    class Ingredient:
        def __init__(self, name, pct):
            self.name, self.weight_pct = name, pct

    class Candidate:
        def __init__(self, n):
            self.ingredients = [Ingredient("环氧树脂", 40 + n), Ingredient("磷酸锌", 8)]

    provenance.ensure_provenance(factory)
    monkeypatch.setattr(provenance, "_session_factory", lambda: factory)
    commits["n"] = 0
    doe_cycle_service._link_runs_to_candidates([f"exp-{i}" for i in range(12)], [Candidate(i) for i in range(7)])
    assert commits["n"] == 1
    assert provenance.count_edges(session_factory=factory) == 12 * 5, "only the first five candidates are linked"


def test_the_doe_cycle_link_step_never_raises(monkeypatch):
    monkeypatch.setattr(provenance, "link_many", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    doe_cycle_service._link_runs_to_candidates(["e1"], [object()])


# ── real processes: no "database is locked", no lost edge ────────────────────


def _writer(db_url: str, wid: int, batches: int, size: int, queue) -> None:
    import os

    os.environ["FORMUMIND_DB_URL"] = db_url
    os.environ["FORMUMIND_API_AUTH_ENABLED"] = "false"
    logging.disable(logging.CRITICAL)
    from app.services import provenance as prov

    ok = True
    for b in range(batches):
        # runs of different writers share candidate formulations, so batches overlap on purpose
        ok &= prov.link_many([("run", f"w{wid}-b{b}-{i}", "formulation", f"f{i % 7}", "tests") for i in range(size)])
        ok &= prov.link("run", f"w{wid}-single-{b}", "formulation", "f0", "tests")
        ok &= prov.link_many([("formulation", f"f{b % 7}", "claim", "shared-claim", "summarised_by")])  # same edge everywhere
    queue.put((wid, bool(ok)))


def test_concurrent_processes_neither_fail_nor_lose_edges(tmp_path, monkeypatch):
    """Spawned (so it also runs where there is no fork - Windows). They start with no provenance tables at all, so the
    creation path races as well."""
    db_url = f"sqlite:///{tmp_path}/race.db"
    monkeypatch.setenv("FORMUMIND_DB_URL", db_url)
    make_engine(db_url).dispose()
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    writers, batches, size = 5, 6, 40
    procs = [ctx.Process(target=_writer, args=(db_url, w, batches, size, queue)) for w in range(writers)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=240)
    assert all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs]
    results = dict(queue.get(timeout=10) for _ in procs)
    assert results == {w: True for w in range(writers)}, "a write was refused (fail-open) under contention"

    con = sqlite3.connect(tmp_path / "race.db")
    try:
        total = con.execute("SELECT COUNT(*) FROM provenance_edges").fetchone()[0]
        runs = con.execute("SELECT COUNT(*) FROM provenance_edges WHERE from_type = 'run'").fetchone()[0]
        shared = con.execute("SELECT COUNT(*) FROM provenance_edges WHERE to_id = 'shared-claim'").fetchone()[0]
    finally:
        con.close()
    assert runs == writers * batches * (size + 1)
    assert shared == len({b % 7 for b in range(batches)}), "the same edge written by every process is one edge"
    assert total == runs + shared

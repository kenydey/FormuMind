"""Text2SQL runs on a private snapshot of what the caller may see, not on the live database (round-5).

The old guard looked for ``project_id = '<id>'`` in the generated SQL. That is a request to the model, and a regex a
statement can satisfy while reading everything: ``WHERE project_id = 'p1' OR 1 = 1`` passed it. It also knew nothing
about owners. Now the rows in scope are copied into an in-memory SQLite database that holds nothing else, so the
assertions here are not "the filter is present" but "no statement, however it is written, can return a row outside the
scope" - checked over a world with three projects, three owners and every table.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.models import Campaign, DOEPlanRow, ExperimentRow, FormulationVersion, MeasurementRow
from app.middleware.api_auth import assert_owner
from app.services import text2sql
from app.services.text2sql import Scope, Text2SQLError, execute_sql, hybrid_answer, open_snapshot, render_schema

QUESTION = "查询实验配方的盐雾平均值"  # structured signal + experiment-data noun -> the structured route

# every row carries a marker in a text column, so "nothing leaked" is checked on the data itself
EXPERIMENTS = {
    # label: (project, owner)
    "e-p1-alice": ("p1", "alice"),
    "e-p1-bob": ("p1", "bob"),
    "e-p1-shared": ("p1", None),
    "e-p2-alice": ("p2", "alice"),
    "e-p2-shared": ("p2", None),
    "e-legacy": ("", None),
}


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path}/world.db")
    Base.metadata.create_all(eng)
    factory = make_session_factory(eng)
    with factory() as s:
        ids = {}
        for label, (project, owner) in EXPERIMENTS.items():
            row = ExperimentRow(
                domain="anticorrosion_coating", project_id=project, owner_id=owner, label=label,
                factors={"zinc": 8.0}, measured={"salt_spray_hours": 500}, created_at=datetime(2026, 9, 1, 12, 30, 15, 250000),
            )
            s.add(row)
            s.flush()
            ids[label] = row.id
            s.add(MeasurementRow(
                id=f"m-{label}", experiment_id=row.id, metric="salt_spray", value=100.0 + len(ids), unit="h",
                test_method="ASTM B117", note=f"note of {label}", passed=len(ids) % 2 == 0,
            ))
        camp = {}
        for name, project, owner in (("c-alice", "p1", "alice"), ("c-bob", "p1", "bob"), ("c-shared", "p2", None)):
            c = Campaign(name=name, project_id=project, owner_id=owner)
            s.add(c)
            s.flush()
            camp[name] = c.id
        for name, project in (("fv-p1", "p1"), ("fv-p2", "p2"), ("fv-none", None)):
            s.add(FormulationVersion(id=name, lineage_id=f"l-{name}", name=name, domain="anticorrosion_coating", project_id=project, snapshot={"name": name}))
        plans = (
            # id, own project, experiment, campaign
            ("plan-A", "p1", None, "c-alice"),
            ("plan-B", "p1", None, "c-bob"),
            ("plan-C", None, "e-p2-alice", None),  # no project of its own: the experiment's
            ("plan-D", None, None, "c-shared"),  # ... or the campaign's
            ("plan-E", "p2", None, None),
            ("plan-F", None, None, None),  # legacy: nowhere
            ("plan-G", "p1", "e-p2-alice", None),  # its own project wins over the experiment's
        )
        for pid, project, exp, c in plans:
            s.add(DOEPlanRow(
                id=pid, design_type="lhs", parameters={"marker": pid}, project_id=project,
                experiment_id=ids.get(exp) if exp else None, campaign_id=camp.get(c) if c else None,
            ))
        s.commit()
    yield eng
    eng.dispose()


def _col(engine, sql: str, *, project=None, owner=None, key=None) -> list:
    rows = execute_sql(engine, sql, scope=Scope(project, owner), max_rows=500)
    return [r[key] if key else next(iter(r.values())) for r in rows]


def _labels(engine, project=None, owner=None) -> set[str]:
    return set(_col(engine, "SELECT label FROM experiments", project=project, owner=owner))


def _plans(engine, project=None, owner=None) -> set[str]:
    return set(_col(engine, "SELECT id FROM doe_plans", project=project, owner=owner))


# ── the scope rules ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "project, owner, experiments, plans",
    [
        ("p1", None, {"e-p1-alice", "e-p1-bob", "e-p1-shared"}, {"plan-A", "plan-B", "plan-G"}),
        ("p2", None, {"e-p2-alice", "e-p2-shared"}, {"plan-C", "plan-D", "plan-E"}),
        ("p3", None, set(), set()),
        # single-user mode answers "default": nothing to restrict, exactly like assert_owner
        ("p1", "default", {"e-p1-alice", "e-p1-bob", "e-p1-shared"}, {"plan-A", "plan-B", "plan-G"}),
        # multi-user: own rows and rows nobody owns
        ("p1", "alice", {"e-p1-alice", "e-p1-shared"}, {"plan-A", "plan-G"}),
        ("p1", "bob", {"e-p1-bob", "e-p1-shared"}, {"plan-B"}),
        ("p1", "carol", {"e-p1-shared"}, set()),
        ("p2", "alice", {"e-p2-alice", "e-p2-shared"}, {"plan-C", "plan-D", "plan-E"}),
        ("p2", "bob", {"e-p2-shared"}, {"plan-D", "plan-E"}),
        # no project chosen: every project, but still only what the owner may see
        (None, "alice", {"e-p1-alice", "e-p1-shared", "e-p2-alice", "e-p2-shared", "e-legacy"}, {"plan-A", "plan-C", "plan-D", "plan-E", "plan-F", "plan-G"}),
        (None, "bob", {"e-p1-bob", "e-p1-shared", "e-p2-shared", "e-legacy"}, {"plan-B", "plan-D", "plan-E", "plan-F"}),
        (None, None, set(EXPERIMENTS), {"plan-A", "plan-B", "plan-C", "plan-D", "plan-E", "plan-F", "plan-G"}),
    ],
)
def test_each_scope_sees_exactly_its_rows(engine, project, owner, experiments, plans):
    assert _labels(engine, project, owner) == experiments
    assert _plans(engine, project, owner) == plans


def test_measurements_follow_their_experiment(engine):
    sql = "SELECT id FROM measurements"
    assert set(_col(engine, sql, project="p1", owner="alice")) == {"m-e-p1-alice", "m-e-p1-shared"}
    assert set(_col(engine, sql, project="p1", owner="bob")) == {"m-e-p1-bob", "m-e-p1-shared"}
    assert set(_col(engine, sql, project="p2")) == {"m-e-p2-alice", "m-e-p2-shared"}
    assert len(_col(engine, sql)) == len(EXPERIMENTS)


def test_formulation_versions_are_project_scoped_only(engine):
    """The table has no owner column, so the owner cannot narrow it - that limit is the schema's, stated in the docs."""
    sql = "SELECT id FROM formulation_versions"
    assert set(_col(engine, sql, project="p1")) == {"fv-p1"}
    assert set(_col(engine, sql, project="p1", owner="bob")) == {"fv-p1"}
    assert set(_col(engine, sql, project="p2")) == {"fv-p2"}
    assert set(_col(engine, sql)) == {"fv-p1", "fv-p2", "fv-none"}


def test_a_plan_is_hidden_when_either_parent_belongs_to_someone_else(engine):
    # plan-G is p1's own, hung on alice's experiment: bob must not see it even though its project is his
    assert "plan-G" in _plans(engine, "p1", "alice")
    assert "plan-G" not in _plans(engine, "p1", "bob")


@pytest.mark.parametrize("resource_owner", [None, "", "alice", "bob"])
@pytest.mark.parametrize("caller", [None, "default", "alice", "bob", "carol"])
def test_the_owner_rule_is_assert_owners_rule(tmp_path, resource_owner, caller):
    eng = make_engine(f"sqlite:///{tmp_path}/own.db")
    with make_session_factory(eng)() as s:
        s.add(ExperimentRow(domain="d", project_id="p", owner_id=resource_owner, label="x", factors={}, measured={}))
        s.commit()
    try:
        assert_owner(resource_owner, caller or "default")
        allowed = True
    except Exception:  # HTTPException 403
        allowed = False
    seen = execute_sql(eng, "SELECT label FROM experiments", scope=Scope("p", caller))
    assert bool(seen) is allowed, (resource_owner, caller)
    eng.dispose()


def test_an_owner_that_is_the_empty_string_is_an_identity_not_a_wildcard(tmp_path):
    """``{"": "token"}`` is a legal tokens map: that caller owns nothing and must not see other people's rows."""
    eng = make_engine(f"sqlite:///{tmp_path}/empty.db")
    with make_session_factory(eng)() as s:
        s.add_all([ExperimentRow(domain="d", project_id="p", owner_id="alice", label="a", factors={}, measured={}),
                   ExperimentRow(domain="d", project_id="p", owner_id=None, label="shared", factors={}, measured={})])
        s.commit()
    assert [r["label"] for r in execute_sql(eng, "SELECT label FROM experiments", scope=Scope("p", ""))] == ["shared"]
    eng.dispose()


# ── nothing a statement says can widen the scope ─────────────────────────────────

ADVERSARIAL = [
    "SELECT label FROM experiments WHERE project_id = 'p1' OR 1 = 1",  # satisfied the old regex, returned everything
    "SELECT label FROM experiments WHERE project_id = 'p2'",
    "SELECT label FROM experiments WHERE project_id != 'p1'",
    "SELECT label FROM experiments WHERE owner_id = 'bob'",
    "SELECT label FROM experiments WHERE 1 = 1 -- project_id = 'p1'",
    "SELECT label FROM experiments /* project_id = 'p1' */",
    "SELECT label FROM main.experiments",
    'SELECT label FROM "experiments"',
    "SELECT label FROM [experiments]",
    "SELECT label FROM `experiments`",
    "SELECT label FROM EXPERIMENTS",
    "SELECT label FROM experiments UNION SELECT label FROM experiments WHERE project_id = 'p2'",
    "SELECT label FROM experiments UNION ALL SELECT name FROM formulation_versions UNION ALL SELECT id FROM doe_plans",
    "SELECT label FROM experiments WHERE id IN (SELECT experiment_id FROM measurements)",
    "SELECT e.label FROM experiments e, measurements m",
    "SELECT e.label FROM experiments e LEFT JOIN measurements m ON m.experiment_id = e.id",
    "SELECT (SELECT group_concat(label) FROM experiments) AS label",
    "SELECT group_concat(label, ' | ') FROM experiments",
    "SELECT json_group_array(label) FROM experiments",
    "WITH RECURSIVE t(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM t WHERE x < 3) SELECT label FROM experiments, t",
    "WITH a AS (SELECT * FROM experiments) SELECT label FROM a",
    "SELECT label FROM experiments ORDER BY (SELECT count(*) FROM experiments e2 WHERE e2.id > experiments.id)",
    "SELECT note FROM measurements",
    "SELECT name FROM formulation_versions",
    "SELECT parameters FROM doe_plans",
    "SELECT label FROM experiments WHERE label LIKE '%'",
]

FOREIGN = ("e-p1-bob", "e-p2-", "e-legacy", "note of e-p1-bob", "note of e-p2", "note of e-legacy", "fv-p2", "fv-none", "plan-B", "plan-C", "plan-D", "plan-E", "plan-F")


@pytest.mark.parametrize("sql", ADVERSARIAL)
def test_no_statement_returns_a_row_outside_the_scope(engine, sql):
    rows = execute_sql(engine, sql, scope=Scope("p1", "alice"), max_rows=500)  # every one of these runs: none is refused
    dump = json.dumps(rows, ensure_ascii=False, default=str)
    for marker in FOREIGN:
        assert marker not in dump, f"{marker!r} leaked through {sql!r}: {dump}"


def test_the_temp_schema_has_no_copy_of_the_tables_to_find(engine):
    with pytest.raises(Text2SQLError, match="temp.experiments"):
        execute_sql(engine, "SELECT label FROM temp.experiments", scope=Scope("p1", "alice"))


def test_a_cte_that_shadows_a_table_can_only_show_what_it_made_up(engine):
    rows = execute_sql(engine, "WITH experiments AS (SELECT 1 AS id, 'made-up' AS label) SELECT label FROM experiments", scope=Scope("p1", "alice"))
    assert rows == [{"label": "made-up"}]


@pytest.mark.parametrize("table, expected", [("experiments", 2), ("measurements", 2), ("formulation_versions", 1), ("doe_plans", 2)])
def test_aggregates_count_the_scope_not_the_database(engine, table, expected):
    assert _col(engine, f"SELECT count(*) FROM {table}", project="p1", owner="alice") == [expected]


def test_every_table_dumped_whole_contains_nothing_foreign(engine):
    for table in text2sql.ALLOWED_TABLES:
        rows = execute_sql(engine, f"SELECT * FROM {table}", scope=Scope("p1", "alice"), max_rows=500)
        dump = json.dumps(rows, ensure_ascii=False, default=str)
        for marker in FOREIGN:
            assert marker not in dump, (table, marker)


@pytest.mark.parametrize(
    "sql, named",
    [
        ("SELECT * FROM source_documents", "source_documents"),
        ("SELECT label FROM experiments WHERE id IN (SELECT id FROM chat_messages)", "chat_messages"),
        ("SELECT name FROM sqlite_master", "sqlite_master"),
        ("SELECT name FROM sqlite_schema", "sqlite_(schema|master)"),  # SQLite reports its alias under the old name
        ("SELECT name FROM sqlite_temp_master", "sqlite_temp_master"),
    ],
)
def test_tables_that_are_not_whitelisted_are_refused_and_named(engine, sql, named):
    with pytest.raises(Text2SQLError, match=named):
        execute_sql(engine, sql, scope=Scope("p1"))


def test_the_schema_functions_are_refused(engine):
    for sql in ("SELECT * FROM pragma_table_info('experiments')", "SELECT * FROM pragma_database_list"):
        with pytest.raises(Text2SQLError):
            execute_sql(engine, sql, scope=Scope("p1"))


@pytest.mark.parametrize("sql", ["SELECT load_extension('x')", "SELECT readfile('/etc/passwd')", "SELECT writefile('/tmp/x', 'y')"])
def test_functions_that_leave_the_database_are_refused(engine, sql):
    with pytest.raises((Text2SQLError, sqlite3.DatabaseError)):
        execute_sql(engine, sql, scope=Scope("p1"))


@pytest.mark.parametrize(
    "sql",
    ["DELETE FROM experiments", "UPDATE experiments SET label = 'x'", "INSERT INTO experiments (id) VALUES (999)",
     "DROP TABLE experiments", "ATTACH DATABASE ':memory:' AS x", "PRAGMA query_only = OFF", "SELECT 1; DELETE FROM experiments"],
)
def test_writes_and_attach_never_get_that_far(engine, sql):
    with pytest.raises(Text2SQLError):
        execute_sql(engine, sql, scope=Scope("p1"))
    assert len(_labels(engine)) == len(EXPERIMENTS)  # the live data is as it was


def test_even_with_no_authorizer_the_engine_refuses_writes_and_attach(engine):
    """Belt and braces: the snapshot is read-only and cannot attach anything, whatever filters ran before it."""
    with open_snapshot(engine, Scope("p1")) as snap:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            snap.conn.execute("INSERT INTO experiments (id, label) VALUES (999, 'x')")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            snap.conn.execute("DELETE FROM experiments")
        with pytest.raises(sqlite3.OperationalError, match="attach"):
            snap.conn.execute("ATTACH DATABASE ':memory:' AS other")
        # and the only database it has is its own, in memory
        assert snap.conn.execute("PRAGMA database_list").fetchall() == [(0, "main", "")]


@pytest.mark.parametrize("sql", ["SELECT length(zeroblob(9000000))", "SELECT length(hex(zeroblob(5000000)))",
                                 "SELECT length(replace(hex(zeroblob(3000000)), '0', 'abc'))"])
def test_a_value_longer_than_the_limit_cannot_be_built_inside_a_statement(engine, sql):
    """SQLite's default longest value is 1 GB: one line of SQL could otherwise exhaust memory inside the time limit."""
    with pytest.raises(sqlite3.DatabaseError, match="too big"):
        execute_sql(engine, sql, scope=Scope("p1"))


def test_the_replace_function_is_not_replace_into(engine):
    assert execute_sql(engine, "SELECT replace(label, 'e-', '') AS l FROM experiments WHERE label = 'e-p1-alice'", scope=Scope("p1")) == [{"l": "p1-alice"}]
    for bad in ("REPLACE INTO experiments (id) VALUES (1)", "WITH x AS (SELECT 1) REPLACE INTO experiments (id) VALUES (1)"):
        with pytest.raises(Text2SQLError):
            execute_sql(engine, bad, scope=Scope("p1"))
    assert len(_labels(engine)) == len(EXPERIMENTS)


# ── time limit ───────────────────────────────────────────────────────────────────

_INFINITE = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) "


def test_a_statement_that_never_finishes_is_stopped(engine):
    t0 = time.monotonic()
    with pytest.raises(Text2SQLError, match="timed out"):
        execute_sql(engine, _INFINITE + "SELECT count(*) FROM c", scope=Scope("p1"), timeout_s=0.3)
    assert time.monotonic() - t0 < 5


def test_the_limit_also_covers_fetching(engine):
    """The first row comes at once, the second never: the work happens while fetching. The handler used to be removed
    before ``fetchmany``, so this ran until the process was killed."""
    t0 = time.monotonic()
    with pytest.raises(Text2SQLError, match="timed out"):
        execute_sql(engine, _INFINITE + "SELECT x FROM c WHERE x = 1 OR x < 0", scope=Scope("p1"), timeout_s=0.3)
    assert time.monotonic() - t0 < 5


# ── the snapshot itself ──────────────────────────────────────────────────────────


def test_the_copy_is_faithful_column_for_column(engine):
    """Same rows, same values - JSON text, timestamps, floats, NULLs, booleans - so queries over it mean what they would
    have meant on the live tables (``json_extract``, ``date('now', ...)`` and so on)."""
    with open_snapshot(engine) as snap, engine.connect() as live:
        for table in text2sql.ALLOWED_TABLES:
            cols = [c["name"] for c in __import__("sqlalchemy").inspect(engine).get_columns(table)]
            query = f"SELECT {', '.join(cols)} FROM {table} ORDER BY {cols[0]}"
            expected = [tuple(r) for r in live.execute(text(query))]
            got = [tuple(r) for r in snap.conn.execute(query)]
            assert got == expected, table
            assert expected, table


def test_json_and_dates_still_work_on_the_copy(engine):
    rows = execute_sql(
        engine,
        "SELECT json_extract(measured, '$.salt_spray_hours') AS h, json_extract(factors, '$.zinc') AS zn, "
        "date(created_at) AS d, typeof(created_at) AS t FROM experiments WHERE label = 'e-p1-alice'",
        scope=Scope("p1", "alice"),
    )
    assert rows == [{"h": 500, "zn": 8.0, "d": "2026-09-01", "t": "text"}]


def test_a_snapshot_is_a_point_in_time_copy(engine):
    with open_snapshot(engine, Scope("p1")) as snap:
        with make_session_factory(engine)() as s:
            s.add(ExperimentRow(domain="d", project_id="p1", label="added-later", factors={}, measured={}))
            s.commit()
        assert "added-later" not in {r["label"] for r in snap.run("SELECT label FROM experiments", max_rows=100)}
    assert "added-later" in _labels(engine, "p1")


def test_the_snapshot_is_closed_afterwards_and_the_live_connection_was_never_touched(engine):
    with open_snapshot(engine, Scope("p1")) as snap:
        conn = snap.conn
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
    with pytest.raises(Text2SQLError):
        execute_sql(engine, "SELECT * FROM source_documents", scope=Scope("p1"))
    with engine.connect() as live:  # no authorizer / progress handler / query_only was left on the pooled connection
        assert live.exec_driver_sql("SELECT count(*) FROM sqlite_master").scalar() > 0
        live.exec_driver_sql("SELECT count(*) FROM source_documents")


def test_each_question_gets_its_own_copy(engine):
    with open_snapshot(engine, Scope("p1")) as a, open_snapshot(engine, Scope("p2")) as b:
        assert a.conn is not b.conn
        assert {r["label"] for r in a.run("SELECT label FROM experiments", max_rows=100)} == {"e-p1-alice", "e-p1-bob", "e-p1-shared"}
        assert {r["label"] for r in b.run("SELECT label FROM experiments", max_rows=100)} == {"e-p2-alice", "e-p2-shared"}


def test_too_many_rows_in_scope_is_refused_not_truncated(engine, monkeypatch):
    """A silently truncated copy would answer AVG() over some of the rows as if it were all of them."""
    monkeypatch.setattr(text2sql, "SNAPSHOT_MAX_ROWS", 3)
    with pytest.raises(Text2SQLError, match="too many"):
        with open_snapshot(engine, Scope(None, None)):
            pass
    out = hybrid_answer(QUESTION, engine, complete_fn=lambda s, u: "SELECT label FROM experiments", evidence=[])
    assert out["route"] == "fallback" and out["rows"] == []


def test_a_legacy_row_that_breaks_a_constraint_the_model_has_is_still_copied(tmp_path):
    """The model says ``test_method`` is NOT NULL; a database that predates that has rows without one. The snapshot
    carries names and types only, so such a row is data, not a reason for the whole question to fail."""
    eng = make_engine(f"sqlite:///{tmp_path}/legacy.db")
    with eng.begin() as conn:
        conn.exec_driver_sql("DROP TABLE measurements")
        conn.exec_driver_sql(
            "CREATE TABLE measurements (id VARCHAR(36) PRIMARY KEY, experiment_id INTEGER, metric TEXT, value REAL, unit TEXT, test_method TEXT)"
        )
        conn.exec_driver_sql("INSERT INTO measurements (id, experiment_id, metric, value) VALUES ('m1', 1, 'salt', 3.5)")
    assert execute_sql(eng, "SELECT id, test_method FROM measurements") == [{"id": "m1", "test_method": None}]
    eng.dispose()


def test_a_table_the_database_lacks_is_simply_not_offered(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path}/partial.db")
    with eng.begin() as conn:
        conn.exec_driver_sql("DROP TABLE doe_plans")
    schema = render_schema(eng)
    assert "doe_plans" not in schema and "experiments" in schema
    with pytest.raises(Text2SQLError, match="doe_plans"):
        execute_sql(eng, "SELECT * FROM doe_plans")
    eng.dispose()


# ── what the model is shown ──────────────────────────────────────────────────────


def test_the_sample_rows_in_the_prompt_come_from_the_same_scope(engine):
    schema = render_schema(engine, project_id="p1", owner_id="alice")
    assert "e-p1-alice" in schema and "e-p1-shared" in schema
    for marker in FOREIGN:
        assert marker not in schema, marker
    assert "CREATE TABLE experiments" in schema and "FOREIGN KEY" in schema  # the model's full DDL still shows the relations


def test_the_sample_rows_of_an_unscoped_question_show_everything(engine):
    schema = render_schema(engine, sample_rows=10)
    assert "e-p1-bob" in schema and "e-p2-alice" in schema


def test_one_huge_value_cannot_fill_the_prompt(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path}/big.db")
    with make_session_factory(eng)() as s:
        s.add(ExperimentRow(domain="d", project_id="p", label="x", factors={}, measured={"blob": "y" * 50_000}))
        s.commit()
    schema = render_schema(eng, project_id="p")
    assert len(schema) < 10_000 and "…" in schema
    eng.dispose()


def test_the_prompt_no_longer_asks_for_a_filter_it_does_not_need():
    system, _ = text2sql.build_sqlite_prompt("q", "schema", project_id="p1")
    assert "不需要" in system and "project_id" in system
    assert "必须按 project_id" not in system
    unscoped, _ = text2sql.build_sqlite_prompt("q", "schema")
    assert "7." not in unscoped


# ── the route end to end ─────────────────────────────────────────────────────────


@pytest.fixture()
def default_db(tmp_path, monkeypatch):
    """The process-wide database, which is what chat reaches: no engine is injected."""
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{tmp_path}/chat.db")
    get_settings.cache_clear()
    from app.db.database import default_engine, default_session_factory

    Base.metadata.create_all(default_engine())
    with default_session_factory()() as s:
        s.add_all([ExperimentRow(domain="d", project_id=p, owner_id=o, label=f"x-{p}-{o}", factors={}, measured={}) for p, o in
                   (("p1", "alice"), ("p1", "bob"), ("p1", None), ("p2", "alice"))])
        s.commit()
    yield
    get_settings.cache_clear()


def _ask(sql, *, project="p1", owner=None, seen=None):
    def complete(system, user):
        if seen is not None:
            seen.append(system)
        return sql

    return hybrid_answer(QUESTION, project_id=project, owner_id=owner, complete_fn=complete, evidence=[])


@pytest.mark.parametrize("sql", ["SELECT label FROM experiments", "SELECT label FROM experiments WHERE project_id = 'p1' OR 1 = 1",
                                 "SELECT label FROM experiments -- project_id = 'p1'"])
def test_a_query_that_forgot_or_gamed_the_project_filter_still_only_sees_the_project(default_db, sql):
    out = _ask(sql, owner="alice")
    assert out["route"] == "structured", out
    assert {r["label"] for r in out["rows"]} == {"x-p1-alice", "x-p1-None"}
    assert "x-p1-bob" not in out["fused_context"] and "x-p2-alice" not in out["fused_context"]


def test_the_sample_rows_the_model_receives_are_scoped_to_the_caller(default_db):
    seen: list[str] = []
    _ask("SELECT label FROM experiments", owner="bob", seen=seen)
    assert "x-p1-bob" in seen[0] and "x-p1-None" in seen[0]
    assert "x-p1-alice" not in seen[0] and "x-p2-alice" not in seen[0]


def test_a_table_outside_the_whitelist_falls_back(default_db):
    out = _ask("SELECT * FROM source_documents")
    assert out["route"] == "fallback" and out["rows"] == []


# ── the copy helpers ─────────────────────────────────────────────────────────────


def test_adapt_stores_values_the_way_sqlite_does(engine):
    from decimal import Decimal
    from datetime import date

    adapt = text2sql._adapt
    assert adapt(None) is None and adapt("é") == "é" and adapt(3) == 3 and adapt(1.5) == 1.5 and adapt(b"\x00") == b"\x00"
    assert adapt(True) is True
    assert adapt(Decimal("2.50")) == 2.5
    assert adapt(date(2026, 9, 1)) == "2026-09-01"
    assert json.loads(adapt({"a": [1, "二"]})) == {"a": [1, "二"]} and "二" in adapt({"a": "二"})
    assert adapt(object.__new__(type("X", (), {"__str__": lambda self: "x!"}))) == "x!"
    # the timestamp format is the one SQLAlchemy itself writes, so dates read from another database compare equal
    stamp = datetime(2026, 9, 1, 12, 30, 15, 250000)
    with engine.connect() as live:
        stored = live.exec_driver_sql("SELECT created_at FROM experiments LIMIT 1").scalar()
    assert stored == adapt(stamp) == "2026-09-01 12:30:15.250000"

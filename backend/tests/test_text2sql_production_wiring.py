"""The Text2SQL route as chat reaches it: no injected engine, the process-wide database (round-4).

``hybrid_answer`` used to read ``default_session_factory().bind`` — a ``sessionmaker`` has no
``.bind`` — so the ``AttributeError`` was swallowed by the fail-open handler and every
structured / hybrid question quietly became ``route == "fallback"``. The route's own tests all
passed an engine in, which is exactly what chat never does. These tests go through the default
engine instead, and pin the guardrails that only matter once the route is actually live:
the table whitelist (it used to exist only in the prompt) and the project scope.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.config import get_settings
from app.db.database import Base, default_engine, default_session_factory
from app.db.models import ExperimentRow
from app.services import text2sql
from app.services.text2sql import Text2SQLError, hybrid_answer

QUESTION = "查询实验配方的盐雾平均值"  # structured signal + experiment-data noun → structured route


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{tmp_path}/t2s.db")
    get_settings.cache_clear()
    Base.metadata.create_all(default_engine())
    with default_session_factory()() as session:
        session.add_all(
            [
                ExperimentRow(domain="anticorrosion_coating", project_id="p1", label="alpha-in-scope", factors={}, measured={"salt_spray_hours": 500}),
                ExperimentRow(domain="anticorrosion_coating", project_id="p2", label="bravo-other-project", factors={}, measured={"salt_spray_hours": 900}),
            ]
        )
        session.commit()
    yield
    get_settings.cache_clear()


def _ask(sql: str, *, project_id: str | None = "p1", seen: list | None = None) -> dict:
    def complete(system: str, user: str) -> str:
        if seen is not None:
            seen.append(system)
        return sql

    return hybrid_answer(QUESTION, project_id=project_id, complete_fn=complete, evidence=[])


def test_the_default_engine_is_what_the_session_factory_is_bound_to(db):
    assert default_session_factory().kw["bind"] is default_engine()
    assert not hasattr(default_session_factory(), "bind")  # the attribute the route used to read


def test_hybrid_answer_reaches_the_database_when_chat_gives_it_no_engine(db):
    out = _ask("SELECT label FROM experiments WHERE project_id = 'p1'")
    assert out["route"] == "structured", out
    assert out["rows"] == [{"label": "alpha-in-scope"}]
    assert "alpha-in-scope" in out["fused_context"]


def test_sample_rows_shown_to_the_model_belong_to_the_project(db):
    seen: list[str] = []
    _ask("SELECT label FROM experiments WHERE project_id = 'p1'", seen=seen)
    assert seen and "alpha-in-scope" in seen[0]
    assert "bravo-other-project" not in seen[0]


def test_without_a_project_the_sample_rows_are_not_filtered(db):
    seen: list[str] = []
    _ask("SELECT label FROM experiments", project_id=None, seen=seen)
    assert "alpha-in-scope" in seen[0] and "bravo-other-project" in seen[0]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM source_documents WHERE project_id = 'p1'",
        "SELECT name FROM sqlite_master WHERE name = 'experiments' AND project_id = 'p1'",
        "SELECT e.label FROM experiments e, source_documents d WHERE e.project_id = 'p1'",
        "SELECT label FROM experiments WHERE project_id = 'p1' AND id IN (SELECT id FROM chat_messages)",
    ],
)
def test_tables_outside_the_whitelist_are_refused_by_the_engine(db, sql):
    # The text filter alone lets every one of these through; the authorizer must not.
    out = _ask(sql)
    assert out["route"] == "fallback", out
    assert out["rows"] == []


def test_execute_sql_names_what_it_refused(db):
    with pytest.raises(Text2SQLError, match="source_documents"):
        text2sql.execute_sql(default_engine(), "SELECT * FROM source_documents")


def test_a_scope_predicate_hidden_in_a_comment_does_not_count(db):
    out = _ask("SELECT label FROM experiments -- project_id = 'p1'")
    assert out["route"] == "fallback", out
    out = _ask("SELECT label FROM experiments /* project_id = 'p1' */")
    assert out["route"] == "fallback", out
    # …but a '--' inside a string literal is data, not a comment
    ok = _ask("SELECT '--' AS dashes FROM experiments WHERE project_id = 'p1'")
    assert ok["route"] == "structured", ok


def test_the_authorizer_is_gone_when_the_query_is_done(db):
    """The connection returns to the pool: an authorizer left on it would break the next borrower."""
    with pytest.raises(Text2SQLError):
        text2sql.execute_sql(default_engine(), "SELECT * FROM source_documents")
    with default_engine().connect() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM sqlite_master").scalar() > 0
    with default_session_factory()() as session:
        assert session.query(ExperimentRow).count() == 2


def test_functions_that_leave_the_database_are_refused(db):
    for sql in ("SELECT load_extension('x')", "SELECT readfile('/etc/passwd')"):
        with pytest.raises((Text2SQLError, sqlite3.DatabaseError)):
            text2sql.execute_sql(default_engine(), sql)

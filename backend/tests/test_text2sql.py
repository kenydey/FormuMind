"""Phase 4: zero-dependency Text2SQL hybrid routing.

Covers: schema rendering (DDL + sample rows, whitelisted tables only),
SQLite prompt shape, SELECT-only guardrail (rejects DROP/DELETE/INSERT/
PRAGMA/multi-statement), server-side LIMIT enforcement, statement timeout,
intent routing, fusion, and the end-to-end scenario
"查询上个月所有除油剂配方中耐蚀性 > 72h 的实验技术路线" on synthetic data.
No langchain dependency anywhere; LLM calls are injected fakes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, ExperimentRow, MeasurementRow
from app.services import text2sql as mod


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def scenario_engine(engine):
    """Synthetic data: degreaser experiments with corrosion measurements."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    try:
        # In scope: last month, corrosion 96h > 72h
        e1 = ExperimentRow(
            domain="除油剂",
            project_id="p1",
            label="除油剂配方A",
            factors={"conc": 5.0},
            measured={"corrosion_h": 96},
            created_at=now - timedelta(days=20),
        )
        # Out of scope: two months ago (fails "上个月")
        e2 = ExperimentRow(
            domain="除油剂",
            project_id="p1",
            label="除油剂配方B",
            factors={"conc": 6.0},
            measured={"corrosion_h": 120},
            created_at=now - timedelta(days=65),
        )
        # Out of scope: 48h < 72h
        e3 = ExperimentRow(
            domain="除油剂",
            project_id="p1",
            label="除油剂配方C",
            factors={"conc": 4.0},
            measured={"corrosion_h": 48},
            created_at=now - timedelta(days=10),
        )
        s.add_all([e1, e2, e3])
        s.flush()
        s.add_all(
            [
                MeasurementRow(
                    id="m1",
                    experiment_id=e1.id,
                    metric="耐蚀性",
                    value=96.0,
                    unit="h",
                    test_method="GB/T 1771",
                ),
                MeasurementRow(
                    id="m2",
                    experiment_id=e2.id,
                    metric="耐蚀性",
                    value=120.0,
                    unit="h",
                    test_method="GB/T 1771",
                ),
                MeasurementRow(
                    id="m3",
                    experiment_id=e3.id,
                    metric="耐蚀性",
                    value=48.0,
                    unit="h",
                    test_method="GB/T 1771",
                ),
            ]
        )
        s.commit()
    finally:
        s.close()
    return engine


# --- schema rendering -----------------------------------------------------


def test_render_schema_whitelist_only(engine):
    text = mod.render_schema(engine)
    for t in ("experiments", "measurements", "formulation_versions", "doe_plans"):
        assert t in text
    # Non-whitelisted tables must not leak into the prompt.
    assert "chat_messages" not in text
    assert "CREATE TABLE" in text


def test_render_schema_includes_sample_rows(scenario_engine):
    text = mod.render_schema(scenario_engine)
    assert "除油剂配方A" in text  # sample rows rendered


def test_build_sqlite_prompt_has_guardrails(engine):
    schema = mod.render_schema(engine)
    system, user = mod.build_sqlite_prompt("查询实验", schema, top_k=20)
    assert "LIMIT 20" in system
    assert "date('now'" in system
    assert "SELECT" in system
    assert user.startswith("问题：")


# --- guardrails ------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "DROP TABLE experiments",
        "DELETE FROM measurements",
        "INSERT INTO experiments VALUES (1)",
        "UPDATE experiments SET label='x'",
        "SELECT * FROM experiments; DROP TABLE measurements",
        "PRAGMA table_info(experiments)",
        "CREATE TABLE evil (id INT)",
        "WITH x AS (SELECT 1) DELETE FROM experiments",
    ],
)
def test_validate_select_only_rejects(bad):
    with pytest.raises(mod.Text2SQLError):
        mod.validate_select_only(bad)


@pytest.mark.parametrize(
    "good",
    [
        "SELECT id FROM experiments",
        "select id, label from experiments where domain='除油剂';",
        "WITH recent AS (SELECT * FROM experiments) SELECT * FROM recent",
    ],
)
def test_validate_select_only_accepts(good):
    assert mod.validate_select_only(good).upper().startswith(("SELECT", "WITH"))


def test_forbidden_keyword_inside_string_literal_ok():
    # A label containing 'delete' must not trip the guardrail.
    sql = "SELECT id FROM experiments WHERE label='please delete me'"
    assert "delete me" in mod.validate_select_only(sql)


def test_enforce_limit_appends_and_clamps():
    assert mod.enforce_limit("SELECT 1", 50).endswith("LIMIT 50")
    assert "LIMIT 50" in mod.enforce_limit("SELECT 1 LIMIT 5000", 50)
    assert "LIMIT 10" in mod.enforce_limit("SELECT 1 LIMIT 10", 50)


def test_execute_sql_end_to_end(scenario_engine):
    rows = mod.execute_sql(
        scenario_engine,
        "SELECT label FROM experiments WHERE domain='除油剂'",
    )
    assert {r["label"] for r in rows} == {"除油剂配方A", "除油剂配方B", "除油剂配方C"}


def test_execute_sql_rejects_dml(scenario_engine):
    with pytest.raises(mod.Text2SQLError):
        mod.execute_sql(scenario_engine, "DELETE FROM experiments")


def test_execute_sql_timeout(scenario_engine):
    # Recursive CTE burns CPU; the progress handler must abort it.
    slow = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT count(*) FROM c"
    with pytest.raises(mod.Text2SQLError, match="timed out"):
        mod.execute_sql(scenario_engine, slow, timeout_s=0.2)


def test_self_check_warnings():
    warns = mod.self_check("SELECT * FROM experiments", "上个月的实验")
    assert any("SELECT *" in w for w in warns)
    assert any("date()" in w for w in warns)
    assert mod.self_check("SELECT id FROM experiments WHERE id=1 LIMIT 5", "查实验1") == []


# --- routing ---------------------------------------------------------------


def test_classify_intent_structured():
    d = mod.classify_intent("查询上个月所有除油剂配方中耐蚀性 > 72h 的实验")
    assert d["route"] == "structured"


def test_classify_intent_unstructured():
    d = mod.classify_intent("除油剂配方耐蚀性提升的方法有哪些？")
    assert d["route"] == "unstructured"
    d2 = mod.classify_intent("盐雾测试的标准流程是什么")
    assert d2["route"] == "unstructured"


def test_fuse_context_marks_sources():
    ctx = mod.fuse_context("q", "SELECT 1", [{"a": 1}], [])
    assert "确定性数据" in ctx
    assert "SELECT 1" in ctx


# --- end-to-end scenario ----------------------------------------------------


def test_scenario_end_to_end(scenario_engine):
    question = "查询上个月所有除油剂配方中耐蚀性 > 72h 的实验技术路线"
    fake_sql = (
        "SELECT e.label, m.value, m.unit FROM experiments e "
        "JOIN measurements m ON m.experiment_id = e.id "
        "WHERE e.domain='除油剂' AND m.metric='耐蚀性' AND m.value > 72 "
        "AND e.created_at >= date('now', '-1 month')"
    )
    fake_evidence = []  # KB empty in this synthetic setup

    out = mod.hybrid_answer(
        question,
        scenario_engine,
        complete_fn=lambda system, user: fake_sql,
        retrieve_fn=lambda *a, **k: fake_evidence,
    )
    assert out["route"] == "structured"
    assert out["sql"] is not None
    labels = [r["label"] for r in out["rows"]]
    # Only 配方A: 配方B is two months old, 配方C is 48h < 72h.
    assert labels == ["除油剂配方A"]
    assert out["rows"][0]["value"] == 96.0
    assert "确定性数据" in out["fused_context"]


def test_hybrid_answer_fail_open_on_llm_error(scenario_engine):
    def _boom(system, user):
        raise RuntimeError("no api key")

    out = mod.hybrid_answer(
        "查询上个月除油剂配方耐蚀性大于72h的实验",
        scenario_engine,
        complete_fn=_boom,
        retrieve_fn=lambda *a, **k: [],
    )
    # Structured path failed -> evidence-only fallback, no exception.
    assert out["route"] == "structured"
    assert out["sql"] is None
    assert out["rows"] == []


def test_hybrid_answer_unstructured_skips_sql(scenario_engine):
    calls = []

    def _should_not_run(system, user):
        calls.append(1)
        return "SELECT 1"

    out = mod.hybrid_answer(
        "盐雾测试的标准流程是什么",
        scenario_engine,
        complete_fn=_should_not_run,
        retrieve_fn=lambda *a, **k: [],
    )
    assert out["route"] == "unstructured"
    assert calls == []
    assert out["sql"] is None

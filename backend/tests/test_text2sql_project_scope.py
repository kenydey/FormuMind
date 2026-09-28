"""Text2SQL project_id scoping.

Covers: prompt rule 7 carries the project filter, require_project_scope
accepts scoped SQL and rejects unscoped SQL (fail-open, no cross-project
leak), and an end-to-end two-project scenario where rows from the other
project never surface. No langchain dependency; LLM calls are fakes.
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


def _two_project_engine(engine):
    """p1 has one corrosion experiment; p2 has a different one."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    try:
        e1 = ExperimentRow(
            domain="除油剂",
            project_id="p1",
            label="除油剂配方A",
            factors={},
            measured={"corrosion_h": 96},
            created_at=now - timedelta(days=20),
        )
        e2 = ExperimentRow(
            domain="除油剂",
            project_id="p2",
            label="除油剂配方X",
            factors={},
            measured={"corrosion_h": 200},
            created_at=now - timedelta(days=10),
        )
        s.add_all([e1, e2])
        s.flush()
        s.add_all(
            [
                MeasurementRow(
                    id="m1",
                    experiment_id=e1.id,
                    metric="耐蚀性",
                    value=96.0,
                    unit="h",
                ),
                MeasurementRow(
                    id="m2",
                    experiment_id=e2.id,
                    metric="耐蚀性",
                    value=200.0,
                    unit="h",
                ),
            ]
        )
        s.commit()
    finally:
        s.close()
    return engine


def test_prompt_carries_project_scope_rule(engine):
    schema = mod.render_schema(engine)
    system, _ = mod.build_sqlite_prompt("q", schema, project_id="p1")
    assert "project_id = 'p1'" in system
    system2, _ = mod.build_sqlite_prompt("q", schema)
    assert "project_id" not in system2 or "7." not in system2


def test_require_project_scope_accepts_scoped():
    sql = "SELECT id FROM experiments WHERE project_id = 'p1' AND domain = 'x'"
    assert mod.require_project_scope(sql, "p1") == sql


def test_require_project_scope_accepts_join_path():
    sql = (
        "SELECT m.value FROM measurements m JOIN experiments e "
        "ON m.experiment_id = e.id WHERE e.project_id = \"p2\""
    )
    assert mod.require_project_scope(sql, "p2") == sql


def test_require_project_scope_rejects_missing():
    with pytest.raises(mod.Text2SQLError):
        mod.require_project_scope("SELECT id FROM experiments", "p1")


def test_require_project_scope_noop_without_project():
    assert mod.require_project_scope("SELECT 1", None) == "SELECT 1"


def test_structured_block_filters_by_project(engine):
    eng = _two_project_engine(engine)
    schema = mod.render_schema(eng)

    def fake_complete(system, user):
        assert "project_id = 'p1'" in system
        return (
            "SELECT label FROM experiments "
            "WHERE project_id = 'p1' AND domain = '除油剂'"
        )

    block, prov = mod.structured_data_block(
        "查询除油剂配方的实验",
        engine=eng,
        project_id="p1",
        complete_fn=fake_complete,
    )
    assert prov["route"] == "structured"
    assert "除油剂配方A" in block
    assert "除油剂配方X" not in block  # other project never surfaces


def test_unscoped_sql_fail_open(engine):
    eng = _two_project_engine(engine)

    def fake_complete(system, user):
        return "SELECT label FROM experiments"  # model forgot the filter

    block, prov = mod.structured_data_block(
        "查询除油剂配方的实验",
        engine=eng,
        project_id="p1",
        complete_fn=fake_complete,
    )
    assert block == ""
    assert prov["route"] == "fallback"  # literature path, answer not blocked

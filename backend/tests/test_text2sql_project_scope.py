"""Text2SQL project scoping.

Covers: the prompt tells the model its database is already scoped, a model that forgets (or games) the project filter
still only sees its own project, and an end-to-end two-project scenario where rows from the other project never
surface. The isolation itself is structural - the statement runs on a snapshot of the project - and is tested
adversarially in ``test_text2sql_snapshot.py``. No langchain dependency; LLM calls are fakes.
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


def test_prompt_tells_the_model_its_database_is_already_scoped(engine):
    schema = mod.render_schema(engine)
    system, _ = mod.build_sqlite_prompt("q", schema, project_id="p1")
    assert "不需要" in system and "project_id" in system
    system2, _ = mod.build_sqlite_prompt("q", schema)
    assert "7." not in system2  # no project chosen: no rule


def test_structured_block_filters_by_project(engine):
    eng = _two_project_engine(engine)
    schema = mod.render_schema(eng)

    def fake_complete(system, user):
        assert "不需要" in system
        return (
            "SELECT label FROM experiments "
            "WHERE project_id = 'p1' AND domain = '除油剂'"
        )

    prov = mod.hybrid_answer(
        "查询除油剂配方的实验",
        engine=eng,
        project_id="p1",
        complete_fn=fake_complete,
        evidence=[],
        include_evidence_text=False,
    )
    assert prov["route"] == "structured"
    assert "除油剂配方A" in prov["fused_context"]
    assert "除油剂配方X" not in prov["fused_context"]  # other project never surfaces


def test_a_query_that_forgot_the_project_filter_still_only_sees_the_project(engine):
    """Used to fall back to the literature path (the text guard refused it); now the data it can see is the project's."""
    eng = _two_project_engine(engine)

    def fake_complete(system, user):
        return "SELECT label FROM experiments"  # model forgot the filter

    out = mod.hybrid_answer(
        "查询除油剂配方的实验",
        engine=eng,
        project_id="p1",
        complete_fn=fake_complete,
        evidence=[],
        include_evidence_text=False,
    )
    assert out["route"] == "structured"
    assert [r["label"] for r in out["rows"]] == ["除油剂配方A"]
    assert "除油剂配方X" not in out["fused_context"]


def test_a_query_that_names_another_project_gets_nothing(engine):
    eng = _two_project_engine(engine)
    out = mod.hybrid_answer(
        "查询除油剂配方的实验",
        engine=eng,
        project_id="p1",
        complete_fn=lambda system, user: "SELECT label FROM experiments WHERE project_id = 'p2'",
        evidence=[],
        include_evidence_text=False,
    )
    assert out["route"] == "structured" and out["rows"] == []
    assert "除油剂配方X" not in out["fused_context"]

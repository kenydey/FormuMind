"""U-4: adopt 双层信号 —— 用户采纳 + 实验验证。

1. record_adopt 支持 copied 信号（第一层补完）；
2. sync 出 lab 测量后，try_mark_experiment_validated 启发式关联被采纳配方
   （同 project、已 adopted、未验证；成分名 Jaccard ≥ 0.8）；
3. 关联不上 → None，fail-open；
4. outcome_stats 的 validated 字段填实。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import recommend_outcome_store
from app.db.models import Base


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        yield s


def _adopt(session, rid="r1", project_id="p1", signal="button", ingredients=None):
    return recommend_outcome_store.record_adopt(
        session,
        recommend_id=rid,
        project_id=project_id,
        adopt_signal=signal,
        formula_snapshot={
            "name": "配方A",
            "ingredients": [{"name": n, "weight_pct": 10.0} for n in (ingredients or ["树脂A", "固化剂B", "溶剂C", "助剂D"])],
            "score": 0.9,
        },
    )


def test_copied_signal_accepted(session) -> None:
    row = _adopt(session, signal="copied")
    assert row.adopt_signal == "copied"
    assert row.adopted is True


def test_validation_marks_matching_outcome(session) -> None:
    _adopt(session)
    rid = recommend_outcome_store.try_mark_experiment_validated(
        session,
        project_id="p1",
        factors={"树脂A": 12.0, "固化剂B": 5.0, "溶剂C": 60.0, "助剂D": 1.0,
                 "cure_temperature_c": 180.0},  # 工艺 lever 不参与匹配
        experiment_id=7,
        measured_keys=["salt_spray_hours"],
    )
    assert rid == "r1"
    row = recommend_outcome_store.record_adopt(session, recommend_id="r1")
    assert row.experiment_validated is True
    assert row.validated_at is not None
    assert row.validation_summary["experiment_id"] == 7
    assert row.validation_summary["measured_keys"] == ["salt_spray_hours"]


def test_validation_no_match_returns_none(session) -> None:
    _adopt(session)
    rid = recommend_outcome_store.try_mark_experiment_validated(
        session,
        project_id="p1",
        factors={"完全不同A": 1.0, "完全不同B": 2.0},
        experiment_id=8,
    )
    assert rid is None


def test_validation_respects_project_scope(session) -> None:
    _adopt(session, rid="r1", project_id="p1")
    rid = recommend_outcome_store.try_mark_experiment_validated(
        session,
        project_id="p2",  # 不同项目
        factors={"树脂A": 12.0, "固化剂B": 5.0, "溶剂C": 60.0, "助剂D": 1.0},
        experiment_id=9,
    )
    assert rid is None


def test_validation_idempotent_second_call_no_double_mark(session) -> None:
    _adopt(session)
    factors = {"树脂A": 12.0, "固化剂B": 5.0, "溶剂C": 60.0, "助剂D": 1.0}
    assert recommend_outcome_store.try_mark_experiment_validated(
        session, project_id="p1", factors=factors, experiment_id=7
    ) == "r1"
    # 已验证的行不再参与匹配
    assert recommend_outcome_store.try_mark_experiment_validated(
        session, project_id="p1", factors=factors, experiment_id=8
    ) is None


def test_validation_fail_open_on_garbage(session) -> None:
    _adopt(session)
    # factors 不是 dict 也不能抛错
    assert recommend_outcome_store.try_mark_experiment_validated(
        session, project_id="p1", factors=None, experiment_id=1
    ) is None


def test_outcome_stats_validated_count(session) -> None:
    _adopt(session, rid="r1")
    _adopt(session, rid="r2", signal="copied")
    recommend_outcome_store.try_mark_experiment_validated(
        session,
        project_id="p1",
        factors={"树脂A": 12.0, "固化剂B": 5.0, "溶剂C": 60.0, "助剂D": 1.0},
        experiment_id=7,
    )
    stats = recommend_outcome_store.outcome_stats(session)
    assert stats["adopted"] == 2
    assert stats["validated"] == 1
    assert stats["by_signal"]["copied"] == 1


def test_validation_recall_metric_subset_factors(session) -> None:
    """P1-2: factors 是快照成分的子集时召回式指标能命中。

    真实数据：F⊆S，Jaccard=|F|/|S|≈0.67 系统性够不上 0.8；
    召回式 |F∩S|/|F|=1.0 命中。
    """
    recommend_outcome_store.record_adopt(
        session,
        recommend_id="r-sub",
        project_id="p1",
        adopt_signal="button",
        formula_snapshot={
            "name": "配方B",
            "ingredients": [{"name": n} for n in
                            ["树脂A", "固化剂B", "溶剂C", "助剂D", "颜料E", "流平剂F"]],
        },
    )
    rid = recommend_outcome_store.try_mark_experiment_validated(
        session,
        project_id="p1",
        factors={"树脂A": 12.0, "固化剂B": 5.0, "溶剂C": 60.0, "助剂D": 1.0},
        experiment_id=11,
    )
    assert rid == "r-sub"


def test_validation_material_map_alias(session) -> None:
    """P1-2: U-5 重命名后的成分名经 material_map 反向归一后命中。"""
    recommend_outcome_store.record_adopt(
        session,
        recommend_id="r-alias",
        project_id="p1",
        adopt_signal="button",
        formula_snapshot={
            "name": "配方C",
            # 快照里是替换后的目标名，factors 里是 lever 原名。
            "ingredients": [{"name": "聚氨酯树脂X"}, {"name": "固化剂B"}],
            "material_map": {"树脂A": {"L1": "聚氨酯树脂X"}},
        },
    )
    rid = recommend_outcome_store.try_mark_experiment_validated(
        session,
        project_id="p1",
        factors={"树脂A": 12.0, "固化剂B": 5.0},
        experiment_id=12,
    )
    assert rid == "r-alias"

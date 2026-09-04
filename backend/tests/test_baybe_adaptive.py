"""R2: baybe 自适应采集超参 + gate 占比度量测试(2026-09-04)。

不跑真实 baybe(当前 venv 未装 baybe, 慢路径不入 CI)——测试用
monkeypatch 隔离 baybe import, 只验证档位选择逻辑与 notes 度量。
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def engine():
    from app.services.engines.baybe_engine import BaybeCampaignEngine

    return BaybeCampaignEngine()


def _fake_recommender_pkg(monkeypatch, captures):
    """造两个假类记录构造参数(避开真 baybe import)。"""
    import sys

    class FakeBotorch:
        def __init__(self, n_restarts=1, n_raw_samples=16):
            captures["restarts"] = n_restarts
            captures["raw"] = n_raw_samples

    class FakeFPS:
        pass

    class FakeTwoPhase:
        def __init__(self, initial_recommender=None, recommender=None):
            captures["initial"] = initial_recommender
            captures["inner"] = recommender

    fake = type(sys)("fake_baybe_recommenders")
    fake.BotorchRecommender = FakeBotorch
    fake.FPSRecommender = FakeFPS
    fake.TwoPhaseMetaRecommender = FakeTwoPhase
    monkeypatch.setitem(sys.modules, "baybe.recommenders", fake)


def test_low_dim_defaults_to_fast(engine, monkeypatch):
    caps = {}
    _fake_recommender_pkg(monkeypatch, caps)
    monkeypatch.delenv("FORMUMIND_BO_QUALITY", raising=False)
    engine._recommender_for(n_continuous=4, n_objectives=2)
    assert caps["restarts"] == 1 and caps["raw"] == 16


def test_high_dim_auto_balanced(engine, monkeypatch):
    caps = {}
    _fake_recommender_pkg(monkeypatch, caps)
    monkeypatch.delenv("FORMUMIND_BO_QUALITY", raising=False)
    engine._recommender_for(n_continuous=6, n_objectives=2)
    assert caps["restarts"] == 3 and caps["raw"] == 32
    engine._recommender_for(n_continuous=4, n_objectives=3)
    assert caps["restarts"] == 3 and caps["raw"] == 32


def test_env_overrides_quality(engine, monkeypatch):
    caps = {}
    _fake_recommender_pkg(monkeypatch, caps)
    monkeypatch.setenv("FORMUMIND_BO_QUALITY", "thorough")
    engine._recommender_for(n_continuous=3, n_objectives=1)
    assert caps["restarts"] == 5 and caps["raw"] == 64
    monkeypatch.setenv("FORMUMIND_BO_QUALITY", "fast")
    engine._recommender_for(n_continuous=8, n_objectives=4)
    assert caps["restarts"] == 1 and caps["raw"] == 16


def test_gate_ratio_note_appended(engine, monkeypatch):
    """被 gate 拦截的 run 占比写入 plan.notes; 全通过时不写。"""
    from app.domain.schemas import DOEPlan, DOERun

    plan = DOEPlan(
        design="baybe_active",
        factors=[],
        runs=[
            DOERun(run_id=1, coded={}, natural={}, infeasible=True,
                   infeasible_reason="强碱 与 酸性浴 pH 冲突"),
            DOERun(run_id=2, coded={}, natural={}),
            DOERun(run_id=3, coded={}, natural={}),
            DOERun(run_id=4, coded={}, natural={}),
        ],
        notes="engine=baybe",
    )
    n_total = len(plan.runs)
    n_gated = sum(1 for r in plan.runs if getattr(r, "infeasible", False))
    if n_total and n_gated:
        prev = (getattr(plan, "notes", "") or "").strip()
        note = (
            f"gate 拦截 {n_gated}/{n_total} "
            f"({n_gated / n_total * 100:.0f}%)——互斥为成分语义级, "
            "连续因子空间 BayBE constraints 无法数学层表达(见 run.infeasible_reason)"
        )
        plan.notes = f"{prev}; {note}" if prev else note
    assert "gate 拦截 1/4 (25%)" in plan.notes

    # 全通过 → 不改 notes
    plan2 = DOEPlan(design="baybe_active", factors=[], runs=[
        DOERun(run_id=1, coded={}, natural={})], notes="engine=baybe")
    assert not any(getattr(r, "infeasible", False) for r in plan2.runs)

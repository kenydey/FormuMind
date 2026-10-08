"""v16 P0-3: API 层 seed 全链透传 —— 端到端可达测试。

验证链路：
  OptimizeRequest.seed → worker payload → workflow.run_optimization(seed)
      → BaybeCampaignEngine.run_optimization(seed) → recommend(seed + round)
  LoopRequest.seed → run_loop_task → loop_iterate(seed)
      → workflow.run_optimization(seed) / active_learning_doe(seed)
  DoeCycleBody.seed → run_doe_cycle_task → run_doe_cycle(seed)
      → generate_experiment_dicts(seed) → _generate_via_baybe(seed)
  active_learning_doe(seed) → baybe.recommend(seed) / legacy build_doe(seed)
"""

from unittest.mock import MagicMock, patch

from app.api.doe import DoeCycleBody
from app.api.optimize import OptimizeRequest
from app.domain.schemas import LoopRequest, Requirement
from app.services.engines.baybe_engine import BaybeCampaignEngine


def _req() -> Requirement:
    return Requirement(
        domain="anticorrosion_coating",
        objectives=[{"metric": "salt_spray_hours", "direction": "maximize", "weight": 1.0}],
    )


def test_v16_optimize_request_accepts_seed():
    r = OptimizeRequest(requirement=_req(), seed=42)
    assert r.seed == 42
    assert OptimizeRequest(requirement=_req()).seed is None


def test_v16_loop_request_accepts_seed():
    r = LoopRequest(**{**_req().model_dump(), "seed": 7})
    assert r.seed == 7


def test_v16_doe_cycle_body_accepts_seed():
    b = DoeCycleBody(requirement=_req(), seed=99)
    assert b.seed == 99


def test_v16_run_optimization_passes_seed_plus_round_to_recommend():
    """多轮场景每轮用 seed+r，保证整体可复现且轮间不重复。"""
    from app.services.engines import baybe_engine as be_mod

    req = _req()
    seen_seeds = []

    fake_result = MagicMock()
    fake_result.plan = MagicMock(runs=[])  # 空 runs → 跳过打分内部循环
    fake_result.campaign_state = "s"
    fake_result.lab_points_used = 0

    def fake_recommend(self, req, **kw):
        seen_seeds.append(kw.get("seed"))
        return fake_result

    settings = MagicMock(top_n_formulas=5)
    with (
        patch.object(BaybeCampaignEngine, "recommend", fake_recommend),
        patch.object(be_mod, "resolve_campaign_objectives", return_value=[]),
        patch.object(be_mod, "process_for", return_value={}),
        patch.object(be_mod.predictor, "default_bounds", return_value={}),
        patch.object(be_mod, "objective_metrics", return_value=["salt_spray_h"]),
        patch.object(be_mod, "get_settings", return_value=settings),
        patch.object(be_mod, "_rank_by_pareto_then_score", return_value=[]),
        patch("app.db.campaign_store.get_campaign_store", return_value=MagicMock()),
        patch("app.services.doe_cycle_service.lab_measurement_source", return_value="lab"),
    ):
        eng = BaybeCampaignEngine()
        eng.run_optimization(req, iterations=12, seed=42)

    # iterations=12 → batch_size=2 → 6 轮，每轮 seed+r
    assert seen_seeds == [42, 43, 44, 45, 46, 47], f"每轮应为 seed+r，实际: {seen_seeds}"


def test_v16_run_optimization_none_seed_stays_none():
    """seed=None 时保持历史行为（OS 熵），不传伪种子。"""
    from app.services.engines import baybe_engine as be_mod

    req = _req()
    seen_seeds = []

    fake_result = MagicMock()
    fake_result.plan = MagicMock(runs=[])
    fake_result.campaign_state = "s"
    fake_result.lab_points_used = 0

    def fake_recommend(self, req, **kw):
        seen_seeds.append(kw.get("seed"))
        return fake_result

    settings = MagicMock(top_n_formulas=5)
    with (
        patch.object(BaybeCampaignEngine, "recommend", fake_recommend),
        patch.object(be_mod, "resolve_campaign_objectives", return_value=[]),
        patch.object(be_mod, "process_for", return_value={}),
        patch.object(be_mod.predictor, "default_bounds", return_value={}),
        patch.object(be_mod, "objective_metrics", return_value=["salt_spray_h"]),
        patch.object(be_mod, "get_settings", return_value=settings),
        patch.object(be_mod, "_rank_by_pareto_then_score", return_value=[]),
        patch("app.db.campaign_store.get_campaign_store", return_value=MagicMock()),
        patch("app.services.doe_cycle_service.lab_measurement_source", return_value="lab"),
    ):
        eng = BaybeCampaignEngine()
        eng.run_optimization(req, iterations=12, seed=None)

    assert seen_seeds == [None] * 6, f"seed=None 应全传 None，实际: {seen_seeds}"


def test_v16_active_learning_doe_passes_seed_to_baybe_recommend():
    from app.domain.schemas import DOEPlan
    from app.services import active_learning

    req = _req()
    captured = {}

    fake_result = MagicMock()
    fake_result.plan = DOEPlan(design="lhs", factors=[], runs=[])
    fake_result.campaign_state = None
    fake_result.engine = "baybe"
    fake_result.strategy_label = "balanced"
    fake_result.strategy_rationale = ""
    fake_result.run_explanations = []
    fake_result.anomalies = []
    fake_result.recommended_next_action = ""
    fake_result.budget_remaining = None
    fake_result.chemical_feasibility = None
    fake_result.physical_constraints = None

    def fake_recommend(self, req, **kw):
        captured.update(kw)
        return fake_result

    with (
        patch.object(BaybeCampaignEngine, "recommend", fake_recommend),
        patch.object(BaybeCampaignEngine, "available", return_value=True),
        patch("app.services.engines.doe_registry.baybe_available", return_value=True),
    ):
        active_learning.active_learning_doe(req, engine="baybe", seed=123)

    assert captured.get("seed") == 123


def test_v16_generate_via_baybe_passes_seed():
    from app.services import doe_cycle_service

    req = _req()
    captured = {}
    fake_engine = MagicMock()
    fake_result = MagicMock()
    fake_result.plan = MagicMock(runs=[])

    def fake_recommend(**kw):
        captured.update(kw)
        return fake_result

    fake_engine.recommend = fake_recommend
    doe_cycle_service._generate_via_baybe(req, [], fake_engine, seed=55)
    assert captured.get("seed") == 55


def test_v16_legacy_lhs_path_receives_seed():
    from app.services import active_learning

    req = _req()
    captured = {}

    def fake_build_doe(r, design="full_factorial", **kw):
        captured.update(kw)
        return MagicMock(runs=[])

    with patch("app.pipeline.workflow.build_doe", fake_build_doe):
        active_learning._legacy_active_learning_doe(req, None, 5, "lhs", seed=77)
    assert captured.get("seed") == 77

"""Regression: DOE cycle LHS fallback must not NameError on dict self-reference."""
from __future__ import annotations

from types import SimpleNamespace


def test_lhs_fallback_reads_infeasible_reason_from_run(monkeypatch):
    """When BayBE is unavailable, LHS path builds exp dicts from ``run`` attrs.

    Pre-fix used ``exp_dict["infeasible_reason"]`` while constructing
    ``exp_dict`` → NameError, caught by the broad except as "Generation failed".
    """
    from app.services import doe_cycle_service as mod

    run = SimpleNamespace(
        run_id="lhs-1",
        coded={"x1": 0.0},
        natural={"resin_wt_pct": 62.0},
        ai_suggested=True,
        infeasible=False,
        infeasible_reason=None,
    )
    active_result = SimpleNamespace(plan=SimpleNamespace(runs=[run]))

    class _UnavailableBaybe:
        def available(self) -> bool:
            return False

    import app.services.engines.baybe_engine as baybe_mod
    import app.services.active_learning as al_mod
    import app.domain.knowledge as knowledge

    monkeypatch.setattr(baybe_mod, "BaybeCampaignEngine", _UnavailableBaybe)
    monkeypatch.setattr(al_mod, "active_learning_doe", lambda **kwargs: active_result)
    monkeypatch.setattr(
        knowledge, "baseline_formulation", lambda req: SimpleNamespace(name="baseline")
    )

    captured: list[dict] = []

    class _ExpRow:
        def __init__(self, **kwargs):
            self.id = "exp-lhs-1"
            captured.append(kwargs)

    class _Session:
        def add(self, obj):
            pass

        def flush(self):
            pass

    class _CM:
        def __enter__(self):
            return _Session()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(mod, "ExperimentRow", _ExpRow)
    monkeypatch.setattr(mod, "default_session_factory", lambda: object())
    monkeypatch.setattr(mod, "commit_session", lambda factory: _CM())

    requirement = SimpleNamespace(
        domain=SimpleNamespace(value="coating"),
        project_id="proj-1",
    )

    result = mod.run_doe_cycle(requirement)

    assert result["status"] == "success", result
    assert result["count"] == 1
    assert captured, "expected ExperimentRow construction"
    # B-9 连带修复：_doe_metadata 嵌套 dict 不再注入 ExperimentRow.factors
    #（相似度评分 `cv > 0` 会 TypeError，且无任何消费者读回它）。factors 只
    # 含数值型 levers；metadata 走日志追踪。
    factors = captured[0]["factors"]
    assert "_doe_metadata" not in factors
    assert factors == {"resin_wt_pct": 62.0}
    assert all(isinstance(v, (int, float)) for v in factors.values())


def test_lhs_fallback_held_when_budget_exhausted(monkeypatch):
    """A-7: 预算耗尽时 LHS 回退也不生成实验点（纵深防御；run_doe_cycle 的
    P2-4 硬停 normally 先拦截，此处覆盖直接调用 generate 路径）。"""
    from types import SimpleNamespace

    from app.services import doe_cycle_service as mod
    import app.services.active_learning as al_mod

    calls = []

    def _should_not_run(**kwargs):
        calls.append(kwargs)
        raise AssertionError("active_learning_doe must not be called on exhausted budget")

    monkeypatch.setattr(al_mod, "active_learning_doe", _should_not_run)
    requirement = SimpleNamespace(
        domain=SimpleNamespace(value="coating"),
        project_id="proj-1",
    )
    for exhausted in (0, -2):
        engine, dicts = mod._generate_via_lhs(
            requirement, [], budget_remaining=exhausted
        )
        assert engine == "lhs"
        assert dicts == []
    assert calls == []


def test_lhs_fallback_propagates_budget_remaining(monkeypatch):
    """A-7: budget_remaining 下沉到 active_learning_doe（元数据/策略感知预算）。"""
    from types import SimpleNamespace

    from app.services import doe_cycle_service as mod
    import app.services.active_learning as al_mod

    captured: dict = {}

    def _fake_active_doe(**kwargs):
        captured.update(kwargs)
        run = SimpleNamespace(
            run_id="lhs-1",
            coded={},
            natural={},
            ai_suggested=True,
            infeasible=False,
            infeasible_reason=None,
        )
        return SimpleNamespace(plan=SimpleNamespace(runs=[run]))

    monkeypatch.setattr(al_mod, "active_learning_doe", _fake_active_doe)
    requirement = SimpleNamespace(
        domain=SimpleNamespace(value="coating"),
        project_id="proj-1",
    )
    engine, dicts = mod._generate_via_lhs(requirement, [], budget_remaining=3)
    assert engine == "lhs"
    assert len(dicts) == 1
    assert captured.get("budget_remaining") == 3

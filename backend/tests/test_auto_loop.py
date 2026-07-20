"""Tests for the self-driving loop orchestration (v0.6, P0)."""
from app.domain.schemas import DOEPlan, LoopReport, OptimizationResult, ProductDomain, Requirement
from app.services.auto_loop import loop_iterate, rmse_plateau_detected

_REQ = Requirement(domain=ProductDomain.anticorrosion_coating, salt_spray_hours=500)


def test_rmse_plateau_detected_when_flat():
    history = [
        {"salt_spray_hours": 0.50, "cost_cny_per_kg": 0.20},
        {"salt_spray_hours": 0.505, "cost_cny_per_kg": 0.201},
        {"salt_spray_hours": 0.504, "cost_cny_per_kg": 0.199},
    ]
    assert rmse_plateau_detected(history, eps=0.01, patience=2)


def test_rmse_plateau_not_detected_when_improving():
    history = [
        {"salt_spray_hours": 0.50},
        {"salt_spray_hours": 0.40},
        {"salt_spray_hours": 0.30},
    ]
    assert not rmse_plateau_detected(history, eps=0.01, patience=2)


def test_loop_iterate_skips_optimize_when_plateau(monkeypatch):
    monkeypatch.setenv("FORMUMIND_LOOP_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_LOOP_CONVERGENCE_EPS", "0.01")
    monkeypatch.setenv("FORMUMIND_LOOP_CONVERGENCE_PATIENCE", "2")
    from app.config import get_settings

    get_settings.cache_clear()

    prior = [
        {"salt_spray_hours": 0.50, "cost_cny_per_kg": 0.20},
        {"salt_spray_hours": 0.505, "cost_cny_per_kg": 0.201},
    ]

    def fake_rmse(domain):
        from app.domain.schemas import ModelInfo

        infos = [
            ModelInfo(domain=domain, metric="salt_spray_hours", rmse=0.504, r2=0.9, n_samples=8, backend="test"),
            ModelInfo(domain=domain, metric="cost_cny_per_kg", rmse=0.199, r2=0.8, n_samples=8, backend="test"),
        ]
        rmse = {m.metric: m.rmse for m in infos}
        return infos, rmse

    monkeypatch.setattr("app.services.auto_loop._rmse_by_metric", fake_rmse)

    stub_opt = OptimizationResult(
        iterations=1,
        objective="salt_spray_hours",
        history=[1.0],
        top_formulations=[],
        engine="test-prior",
    )
    stub_doe = DOEPlan(design="lhs", factors=[], runs=[], plan_id="prior")

    rep = loop_iterate(
        _REQ,
        optimize_iterations=6,
        n_suggest=3,
        prior_rmse_history=prior,
        prior_optimization=stub_opt,
        prior_next_doe=stub_doe,
    )
    assert rep.converged is True
    assert rep.loop_message
    assert rep.optimization.engine == "test-prior"
    assert rep.next_doe.plan_id == "prior"
    get_settings.cache_clear()


def test_loop_iterate_includes_adaptive_fields():
    rep = loop_iterate(_REQ, optimize_iterations=6, n_suggest=3)
    assert rep.strategy_label in {"exploration", "balanced", "exploitation"}
    assert rep.recommended_next_action
    assert len(rep.run_explanations) >= 1


def test_loop_iterate_returns_loop_report():
    rep = loop_iterate(_REQ, optimize_iterations=6, n_suggest=3)
    assert isinstance(rep, LoopReport)
    assert rep.domain == "anticorrosion_coating"
    assert rep.engine in {"numpy-ucb", "optuna-tpe", "summit-sobo", "botorch-ei"}


def test_loop_iterate_produces_optimization_and_next_doe():
    rep = loop_iterate(_REQ, optimize_iterations=6, n_suggest=4)
    # Optimization should yield ranked top formulations
    assert len(rep.optimization.top_formulations) > 0
    assert len(rep.optimization.history) == 6
    # Active-learning DOE should flag exactly n_suggest runs
    ai_runs = [r for r in rep.next_doe.runs if r.ai_suggested]
    assert len(ai_runs) == 4


def test_loop_iterate_graceful_without_records():
    # In the CI baseline there may be no lab data — loop must still return a result.
    rep = loop_iterate(_REQ, optimize_iterations=4, n_suggest=2)
    assert rep.total_records >= 0
    assert isinstance(rep.rmse_by_metric, dict)
    assert rep.next_doe.design  # a design was chosen


def test_loop_progress_callback_invoked():
    calls: list[tuple[float, str]] = []
    loop_iterate(_REQ, optimize_iterations=4, n_suggest=2, progress_cb=lambda p, m: calls.append((p, m)))
    assert calls, "progress callback should be invoked at least once"
    assert calls[-1][0] == 1.0, "final progress should reach 1.0"


def test_records_for_filters_by_domain():
    from app.services.training import registry

    recs = registry.records_for(ProductDomain.anticorrosion_coating)
    assert isinstance(recs, list)
    assert all(r.domain == ProductDomain.anticorrosion_coating for r in recs)


def test_loop_all_domains():
    for domain in ProductDomain:
        rep = loop_iterate(Requirement(domain=domain), optimize_iterations=4, n_suggest=2)
        assert rep.optimization.top_formulations
        assert rep.next_doe.runs

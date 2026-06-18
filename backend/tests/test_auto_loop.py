"""Tests for the self-driving loop orchestration (v0.6, P0)."""
from app.domain.schemas import LoopReport, ProductDomain, Requirement
from app.services.auto_loop import loop_iterate

_REQ = Requirement(domain=ProductDomain.anticorrosion_coating, salt_spray_hours=500)


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

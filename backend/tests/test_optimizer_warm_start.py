"""Optimizer warm start with the baseline recipe (found by adding optuna to CI).

With ``optuna`` installed ``build_optimizer`` picks Optuna's TPE, whose first ten
trials are random; ``run_optimization`` only ever scored *suggested* points, so a
6-iteration run could return an "optimized" recipe worse than the baseline
(1006.2 < 1014.9 in ``test_pipeline``). Two defects sat behind it:

* the incumbent (baseline) recipe was never evaluated / observed;
* ``OptunaOptimizer.observe`` for a point that did not come from ``suggest()``
  called ``suggest_float`` on a fresh trial — drawing *new* parameters — so the
  study learned the score against points that were never ``x``.
"""
from __future__ import annotations

import pytest

from app.domain import knowledge
from app.domain.project_spec import normalize_requirement, resolve_levers
from app.domain.schemas import (
    Formulation,
    Ingredient,
    LeverSpec,
    ObjectiveSpec,
    ProductDomain,
    Requirement,
    Substrate,
)
from app.pipeline import reconstruct, workflow
from app.services.optimizer import BayesianOptimizer, Factor


# ── baseline_lever_values: the inverse of formulation_from_factors ───────────


def _base(*ings: tuple[str, float]) -> Formulation:
    return Formulation(
        name="b",
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[Ingredient(name=n, role="resin", weight_pct=w) for n, w in ings],
    )


def test_baseline_lever_values_reads_weights_process_and_unit_conversion():
    levers = [
        LeverSpec(name="Resin", low=10, high=60, unit="wt%"),
        LeverSpec(name="Cerium nitrate", low=1, high=15, unit="g/L"),  # 0.5 wt% == 5 g/L
        LeverSpec(name="cure_temperature_c", low=80, high=140, unit="C"),
    ]
    vals = reconstruct.baseline_lever_values(
        levers, _base(("Resin", 38.0), ("Cerium nitrate", 0.5)), {"cure_temperature_c": 120.0}
    )
    assert vals == {"Resin": 38.0, "Cerium nitrate": pytest.approx(5.0), "cure_temperature_c": 120.0}


def test_baseline_lever_values_unknown_baseline_is_the_midpoint_and_values_are_clipped():
    levers = [
        LeverSpec(name="immersion_time_min", low=30, high=300, unit="min"),  # no process value
        LeverSpec(name="Not in base", low=2, high=4, unit="wt%"),
        LeverSpec(name="Resin", low=10, high=20, unit="wt%"),  # base weight 38 → clipped
    ]
    vals = reconstruct.baseline_lever_values(levers, _base(("Resin", 38.0)), {})
    assert vals["immersion_time_min"] == 165.0
    assert vals["Not in base"] == 3.0
    assert vals["Resin"] == 20.0


@pytest.mark.parametrize(
    "req",
    [
        Requirement(domain=ProductDomain.anticorrosion_coating, cure_temperature_c=120, salt_spray_hours=500),
        Requirement(domain=ProductDomain.surface_treatment, substrate=Substrate.magnesium_alloy),
        Requirement(domain=ProductDomain.degreaser),
    ],
)
def test_baseline_lever_values_round_trip_through_formulation_from_factors(req):
    """Evaluating the warm-start vector must reproduce the baseline recipe."""
    req = normalize_requirement(req)
    base = req.active_formulation or knowledge.baseline_formulation(req)
    levers = resolve_levers(req, base)
    vals = reconstruct.baseline_lever_values(levers, base, workflow.process_for(req))
    rebuilt = reconstruct.formulation_from_factors(req, vals)
    base_w = {i.name: i.weight_pct for i in base.ingredients}
    rebuilt_w = {i.name: i.weight_pct for i in rebuilt.ingredients}
    for lev in levers:
        if lev.name in base_w:
            assert rebuilt_w[lev.name] == pytest.approx(base_w[lev.name], abs=0.01), lev.name


# ── OptunaOptimizer.observe records the real (x, y) ──────────────────────────


def test_optuna_observe_records_the_real_point_for_a_non_suggested_x():
    pytest.importorskip("optuna")
    from app.services.optimizer import OptunaOptimizer

    factors = [Factor("a", 0.0, 10.0), Factor("b", 5.0, 6.0)]
    opt = OptunaOptimizer(factors=factors, seed=1)

    opt.observe([2.5, 5.5], 0.75)

    trials = opt._study.trials
    assert len(trials) == 1  # no spurious extra trial
    assert trials[0].params == {"a": 2.5, "b": 5.5}  # the real x, not a fresh draw
    assert trials[0].value == 0.75
    assert opt.best == ([2.5, 5.5], 0.75)


def test_optuna_observe_clips_out_of_range_points_and_keeps_suggested_flow():
    pytest.importorskip("optuna")
    from app.services.optimizer import OptunaOptimizer

    opt = OptunaOptimizer(factors=[Factor("a", 0.0, 1.0)], seed=1)
    opt.observe([5.0], 0.1)  # outside the box → clipped into it, not an error
    assert opt._study.trials[0].params == {"a": 1.0}

    x = opt.suggest()
    opt.observe(x, 0.9)  # the normal suggest → observe flow still tells the pending trial
    assert len(opt._study.trials) == 2
    assert opt._study.trials[1].params["a"] == pytest.approx(x[0])
    assert opt.ranked(1)[0][1] == 0.9


def test_optuna_study_learns_from_a_warm_start():
    """The injected observation is part of the study TPE samples from."""
    pytest.importorskip("optuna")
    from app.services.optimizer import OptunaOptimizer

    opt = OptunaOptimizer(factors=[Factor("a", 0.0, 1.0)], seed=3)
    opt.observe([0.42], 123.0)
    best = opt._study.best_trial
    assert best.params["a"] == 0.42 and best.value == 123.0


# ── run_optimization warm-starts the optimizer with the baseline ─────────────


class _RecordingOptimizer(BayesianOptimizer):
    events: list = []

    def suggest(self, n_candidates: int = 64, kappa: float = 1.5):
        x = super().suggest(n_candidates, kappa)
        type(self).events.append(("suggest", list(x)))
        return x

    def observe(self, x, y):
        type(self).events.append(("observe", list(x), float(y)))
        super().observe(x, y)


def test_run_optimization_observes_the_baseline_before_the_first_suggestion(monkeypatch):
    _RecordingOptimizer.events = []
    monkeypatch.setattr(
        workflow,
        "build_optimizer",
        lambda factors, seed=0: _RecordingOptimizer(factors=factors, seed=seed),
    )
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        cure_temperature_c=120,
        salt_spray_hours=500,
        objectives=[ObjectiveSpec(metric="salt_spray_hours", weight=1.0, direction="maximize")],
    )
    result = workflow.run_optimization(req, iterations=3, engine="numpy", existing_records=[])

    events = _RecordingOptimizer.events
    assert events[0][0] == "observe", "the baseline must be observed before any suggestion"
    norm = normalize_requirement(req)
    base = norm.active_formulation or knowledge.baseline_formulation(norm)
    levers = resolve_levers(norm, base)
    expected = reconstruct.baseline_lever_values(levers, base, workflow.process_for(norm))
    assert events[0][1] == [expected[l.name] for l in levers]
    # the warm start is not one of the `iterations` suggested experiments
    assert [e[0] for e in events].count("suggest") == 3
    assert len(result.history) == 3
    assert all(b >= a for a, b in zip(result.history, result.history[1:]))


def test_the_returned_best_is_never_worse_than_the_baseline(monkeypatch):
    """Even with an optimizer that only ever proposes the worst corner."""
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        cure_temperature_c=120,
        salt_spray_hours=500,
        objectives=[ObjectiveSpec(metric="salt_spray_hours", weight=1.0, direction="maximize")],
    )
    norm = normalize_requirement(req)
    base = norm.active_formulation or knowledge.baseline_formulation(norm)
    levers = resolve_levers(norm, base)

    class _WorstCorner(BayesianOptimizer):
        def suggest(self, n_candidates: int = 64, kappa: float = 1.5):
            # every lever at its lower bound — here a clear downgrade vs. baseline
            return [f.low for f in self.factors]

    monkeypatch.setattr(
        workflow, "build_optimizer", lambda factors, seed=0: _WorstCorner(factors=factors, seed=seed)
    )
    result = workflow.run_optimization(req, iterations=3, engine="numpy", existing_records=[])

    from app.services import predictor

    baseline_score = predictor.objective_value(
        reconstruct.formulation_from_factors(
            norm, reconstruct.baseline_lever_values(levers, base, workflow.process_for(norm))
        ),
        result.objective,
        workflow.process_for(norm),
    )
    assert result.top_formulations[0].score >= baseline_score - 1e-6

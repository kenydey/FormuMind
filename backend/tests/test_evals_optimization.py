"""The optimisation suite (``backend/evals``): test functions with known optima, and a study that can tell an oracle from a
stuck optimiser. The engines' own quality is a *result* of the study (``evals/baselines``), not something asserted here beyond
the one thing a unit test can pin without being flaky: that every engine is repeatable and stays inside the box.
"""
from __future__ import annotations

import numpy as np
import pytest
from evals import benchmarks as B
from evals.suites import optimization as O


class Oracle:
    """Suggests the optimum every time (a stand-in for a perfect engine)."""

    def __init__(self, point):
        self.point = [float(v) for v in point]

    def suggest(self):
        return self.point

    def observe(self, x, y):
        pass


class Stuck:
    """Suggests the same corner every time."""

    def __init__(self, dim):
        self.dim = dim

    def suggest(self):
        return [0.0] * self.dim

    def observe(self, x, y):
        pass


OPTIMA = {
    "branin": [(5 - np.pi) / 15, 12.275 / 15],
    "hartmann3": [0.114614, 0.555649, 0.852547],
    "hartmann6": [0.20169, 0.150011, 0.476874, 0.275332, 0.311652, 0.6573],
    "ridge3": [0.5, 0.3, 0.2],
}


@pytest.mark.parametrize("bench", B.BENCHMARKS, ids=lambda b: b.name)
def test_each_function_reaches_its_stated_optimum_where_the_literature_puts_it(bench):
    assert bench.f(np.array(OPTIMA[bench.name])) == pytest.approx(bench.optimum, abs=1e-4)


@pytest.mark.parametrize("bench", B.BENCHMARKS, ids=lambda b: b.name)
def test_nothing_exceeds_the_optimum_and_a_random_point_is_far_below_it(bench):
    rng = np.random.default_rng(0)
    values = [bench.f(rng.random(bench.dim)) for _ in range(3000)]
    assert max(values) <= bench.optimum + 1e-9
    assert bench.reference_level() < bench.optimum - 0.2 * abs(bench.optimum - bench.reference_level())


def test_normalised_regret_is_zero_at_the_optimum_one_at_the_random_average_and_never_negative():
    bench = B.by_name("hartmann3")
    ref = bench.reference_level()
    assert bench.normalised_regret(bench.optimum, ref) == 0.0
    assert bench.normalised_regret(ref, ref) == pytest.approx(1.0)
    assert bench.normalised_regret(bench.optimum + 1, ref) == 0.0


def test_the_halton_design_is_extensible_inside_the_cube_and_shifted_by_the_seed():
    shift = np.array([0.1, 0.2, 0.3])
    long = O.halton_points(20, 3, shift)
    assert np.array_equal(O.halton_points(8, 3, shift), long[:8])  # every prefix is a design of its own
    assert long.min() >= 0.0 and long.max() < 1.0
    assert not np.array_equal(long, O.halton_points(20, 3, np.zeros(3)))
    assert len({tuple(row) for row in long}) == 20


def test_an_oracle_has_no_regret_and_a_stuck_engine_has_a_lot():
    bench = B.by_name("ridge3")
    oracle = O.run_engine(lambda factors, seed: Oracle(OPTIMA["ridge3"]), bench, seed=0, noise_sd=0.0, budget=6)
    stuck = O.run_engine(lambda factors, seed: Stuck(3), bench, seed=0, noise_sd=0.0, budget=6)
    ref = bench.reference_level()
    assert O._regrets([oracle], bench, ref, 6) == [0.0]
    assert O._regrets([stuck], bench, ref, 6)[0] > 0.1


def test_the_incumbent_is_the_best_observed_point_even_when_noise_misleads():
    """Regret is scored on the true value of the point the optimiser would hand over - the best *observed* one."""
    bench = B.by_name("ridge3")
    best, worse = [0.5, 0.3, 0.2], [0.0, 0.0, 0.0]

    class Noise:  # the first measurement reads far too low, the second far too high (the worse point is 20 below the optimum)
        def __init__(self):
            self.draws = iter([-30.0, +30.0])

        def normal(self, loc, scale):
            return next(self.draws)

    def curve(points, noise_sd, rng):
        queue = iter(points)
        return O._incumbent_curve(lambda: next(queue), None, bench, noise_sd, rng, budget=len(points)).curve

    assert curve([best, worse], 0.0, np.random.default_rng(0)) == [bench.optimum, bench.optimum]  # no noise: the better point stays
    misled = curve([best, worse], 1.0, Noise())
    assert misled[0] == bench.optimum  # the first point is the only one seen so far
    assert misled[1] == bench.f(np.asarray(worse))  # measured high, so it replaces the better one - and its TRUE value is what is scored


@pytest.mark.parametrize("name", sorted(O.engines()))
def test_every_engine_is_repeatable_and_stays_inside_the_box(name):
    bench = B.by_name("hartmann3")
    make = O.engines()[name]
    first = O.run_engine(make, bench, seed=3, noise_sd=0.0, budget=10)
    assert first.curve == O.run_engine(make, bench, seed=3, noise_sd=0.0, budget=10).curve
    optimiser = make(O._factors(3), 0)
    for _ in range(8):
        x = optimiser.suggest()
        assert all(0.0 <= v <= 1.0 for v in x)
        optimiser.observe(x, bench.f(np.asarray(x)))


def test_paired_comparison_has_the_sign_a_reader_expects():
    better = O._compare([0.1, 0.1, 0.2], [0.5, 0.4, 0.6])
    assert better["advantage"] > 0 and better["win_rate"] == 1.0 and better["advantage_ci"][0] > 0
    worse = O._compare([0.5, 0.4], [0.1, 0.1])
    assert worse["advantage"] < 0 and worse["win_rate"] == 0.0
    assert O._compare([0.3], [0.3])["win_rate"] == 0.5


def test_the_study_has_the_structure_the_report_reads():
    result = O.run(budget=8, seeds=2, noise=(0.0,), benchmarks=(B.by_name("ridge3"),), engine_names=["numpy-ucb"])
    table = result["results"]["0.0"]["ridge3"]
    assert set(table) == {"random", "halton", "numpy-ucb"}
    assert "vs_random" not in table["random"] and set(table["numpy-ucb"]["vs_random"]) == {"advantage", "advantage_ci", "win_rate"}
    assert table["numpy-ucb"]["seeds"] == 2 and set(table["numpy-ucb"]["regret_at"]) == {"5"}  # checkpoints beyond the budget are dropped
    assert set(result["summary"]["0.0"]) == {"random", "halton", "numpy-ucb"}
    assert result["config"]["budget"] == 8 and result["config"]["engines"] == ["numpy-ucb"]


def test_a_better_engine_shows_up_as_a_positive_advantage_over_random_search():
    ridge = B.by_name("ridge3")
    result = O.run(budget=10, seeds=4, noise=(0.0,), benchmarks=(ridge,),
                   makers={"oracle": lambda factors, seed: Oracle(OPTIMA["ridge3"])})
    row = result["results"]["0.0"]["ridge3"]["oracle"]
    assert row["final_regret"]["mean"] == 0.0 and row["vs_random"]["advantage"] > 0 and row["vs_random"]["win_rate"] == 1.0


def test_the_slow_engine_runs_only_when_it_is_named(monkeypatch):
    from app.services import optimizer

    monkeypatch.setattr(optimizer, "_botorch_available", lambda: True)
    assert "botorch-ei" not in O.engines()  # whether torch happens to be installed must not change a default run
    assert "botorch-ei" in O.engines(["botorch-ei"])
    with pytest.raises(ValueError, match="not available here"):
        O.engines(["no-such-engine"])

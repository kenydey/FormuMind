"""Optimisation: for the same number of experiments, does a recommended one get closer to the optimum than a random one?

Each engine the application can run (``services/optimizer.py``: the numpy UCB default, Optuna's TPE, BoTorch's GP-EI when
installed) drives the same sequential loop - ``suggest`` an experiment, run it on a test function with known optimum, add
measurement noise, ``observe`` - for ``BUDGET`` runs, over ``SEEDS`` seeds, on the four functions in ``evals/benchmarks.py``.
Two baselines run the same loop: uniform random points, and a randomly shifted Halton sequence (a space-filling design that
needs no model). An optimiser that cannot beat these is spending the chemist's experiments on nothing.

Scores are on the *true* value of the incumbent - the best point by observed (noisy) value, which is what would be handed to
a chemist - as a **normalised regret**: 0 at the optimum, 1 at the average of random points. The headline per engine is the
paired difference to random search (same seed, same noise stream): positive means the engine is ahead, with a bootstrap
interval, because "slightly lower regret" over ten seeds is not a result.
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from .. import metrics as M
from ..benchmarks import BENCHMARKS, Benchmark

BUDGET = 30
SEEDS = 10
CHECKPOINTS = (5, 10, 20, 30)
NOISE_LEVELS = (0.0, 0.03)  # measurement noise as a share of (optimum - random average)
SLOW_ENGINE_SEEDS = {"botorch-ei": 3}  # a GP fit per suggestion: minutes per run otherwise

Maker = Callable[[list, int], object]
_PRIMES = (2, 3, 5, 7, 11, 13, 17, 19)


def engines(names: Sequence[str] | None = None) -> dict[str, Maker]:
    """The optimisers to run, by name: those named, or - by default - the fast ones this installation has.

    ``botorch-ei`` fits a Gaussian process for every suggestion (about ten seconds a run, four minutes for the study), so it only
    runs when asked for by name; whether torch happens to be installed must not change what a default run measures.
    """
    from app.services import optimizer as O

    available: dict[str, Maker] = {"numpy-ucb": lambda factors, seed: O.BayesianOptimizer(factors=factors, seed=seed)}
    if O._optuna_available():
        available["optuna-tpe"] = lambda factors, seed: O.OptunaOptimizer(factors=factors, seed=seed)
    if O._botorch_available():
        available["botorch-ei"] = lambda factors, seed: O.BotorchOptimizer(factors=factors, seed=seed)
    if names is None:
        return {k: v for k, v in available.items() if k not in SLOW_ENGINE_SEEDS}
    unknown = [n for n in names if n not in available]
    if unknown:
        raise ValueError(f"not available here: {', '.join(unknown)} (have: {', '.join(sorted(available))})")
    return {n: available[n] for n in names}


def _factors(dim: int) -> list:
    from app.services.optimizer import Factor

    return [Factor(f"x{i}", 0.0, 1.0) for i in range(dim)]


def halton_points(n: int, dim: int, shift: np.ndarray) -> np.ndarray:
    """First ``n`` points of the Halton sequence, rotated by ``shift`` (mod 1): extensible, so every prefix is space-filling."""
    points = np.zeros((n, dim))
    for d in range(dim):
        base = _PRIMES[d]
        for i in range(n):
            f, r, k = 1.0, 0.0, i + 1
            while k > 0:
                f /= base
                r += f * (k % base)
                k //= base
            points[i, d] = r
    return (points + shift) % 1.0


# ── one run ──────────────────────────────────────────────────────────────────────────────────────


@dataclass
class Run:
    curve: list[float]  # true value of the incumbent after each experiment


def _incumbent_curve(proposals: Callable[[], Sequence[float]], observe: Callable | None, bench: Benchmark, noise_sd: float,
                     noise_rng: np.random.Generator, budget: int) -> Run:
    best_observed, best_true = -math.inf, -math.inf
    curve: list[float] = []
    for _ in range(budget):
        x = np.asarray(proposals(), dtype=float)
        true = bench.f(x)
        observed = true + float(noise_rng.normal(0.0, noise_sd)) if noise_sd else true
        if observe is not None:
            observe(list(map(float, x)), observed)
        if observed > best_observed:
            best_observed, best_true = observed, true
        curve.append(best_true)
    return Run(curve)


def run_engine(make: Maker, bench: Benchmark, seed: int, noise_sd: float, budget: int = BUDGET) -> Run:
    optimiser = make(_factors(bench.dim), seed)
    return _incumbent_curve(optimiser.suggest, optimiser.observe, bench, noise_sd, np.random.default_rng(10_000 + seed), budget)


def run_random(bench: Benchmark, seed: int, noise_sd: float, budget: int = BUDGET) -> Run:
    rng = np.random.default_rng(20_000 + seed)
    return _incumbent_curve(lambda: rng.random(bench.dim), None, bench, noise_sd, np.random.default_rng(10_000 + seed), budget)


def run_halton(bench: Benchmark, seed: int, noise_sd: float, budget: int = BUDGET) -> Run:
    shift = np.random.default_rng(30_000 + seed).random(bench.dim)
    points = iter(halton_points(budget, bench.dim, shift))
    return _incumbent_curve(lambda: next(points), None, bench, noise_sd, np.random.default_rng(10_000 + seed), budget)


# ── the study ────────────────────────────────────────────────────────────────────────────────────


def _regrets(runs: list[Run], bench: Benchmark, reference: float, at: int) -> list[float]:
    return [bench.normalised_regret(r.curve[at - 1], reference) for r in runs]


def _compare(candidate: list[float], baseline: list[float]) -> dict:
    """Paired by seed: positive ``advantage`` = the candidate ended with the smaller regret."""
    diffs = [b - c for c, b in zip(candidate, baseline, strict=True)]
    lo, hi = M.bootstrap_ci(diffs)
    return {
        "advantage": round(M.mean(diffs), 4),
        "advantage_ci": [round(lo, 4), round(hi, 4)],
        "win_rate": round(M.win_rate(candidate, baseline), 4),
    }


def run(
    *,
    budget: int = BUDGET,
    seeds: int = SEEDS,
    noise: Sequence[float] = NOISE_LEVELS,
    benchmarks: Sequence[Benchmark] = BENCHMARKS,
    engine_names: Sequence[str] | None = None,
    makers: dict[str, Maker] | None = None,
) -> dict:
    makers = engines(engine_names) if makers is None else makers
    checkpoints = tuple(c for c in CHECKPOINTS if c <= budget)
    results: dict[str, dict] = {}
    for level in noise:
        per_bench: dict[str, dict] = {}
        for bench in benchmarks:
            reference = bench.reference_level()
            noise_sd = level * (bench.optimum - reference)
            runs: dict[str, list[Run]] = {
                "random": [run_random(bench, s, noise_sd, budget) for s in range(seeds)],
                "halton": [run_halton(bench, s, noise_sd, budget) for s in range(seeds)],
            }
            for name, make in makers.items():
                n_seeds = min(seeds, SLOW_ENGINE_SEEDS.get(name, seeds))
                runs[name] = [run_engine(make, bench, s, noise_sd, budget) for s in range(n_seeds)]
            table: dict[str, dict] = {}
            for name, rs in runs.items():
                final = _regrets(rs, bench, reference, budget)
                row = {
                    "seeds": len(rs),
                    "final_regret": M.summarise(final),
                    "regret_at": {str(c): round(M.mean(_regrets(rs, bench, reference, c)), 4) for c in checkpoints},
                }
                if name != "random":
                    n = len(rs)
                    row["vs_random"] = _compare(final, _regrets(runs["random"][:n], bench, reference, budget))
                table[name] = row
            per_bench[bench.name] = table
        results[str(level)] = per_bench

    return {"suite": "optimization", "config": _config(budget, seeds, noise, makers), "results": results, "summary": _summary(results, makers)}


def _config(budget: int, seeds: int, noise: Sequence[float], makers: dict) -> dict:
    config: dict = {"budget": budget, "seeds": seeds, "noise": list(noise), "engines": sorted(makers),
                    "benchmarks": [b.name for b in BENCHMARKS], "slow_engine_seeds": SLOW_ENGINE_SEEDS}
    try:
        import optuna

        config["optuna"] = optuna.__version__
    except ImportError:
        pass
    return config


def _summary(results: dict, makers: dict) -> dict:
    """Per engine, over every benchmark: mean final regret, mean paired advantage over random search, benchmarks clearly ahead."""
    summary: dict[str, dict] = {}
    for level, per_bench in results.items():
        by_engine: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for table in per_bench.values():
            for name, row in table.items():
                by_engine[name]["regret"].append(row["final_regret"]["mean"])
                if "vs_random" in row:
                    by_engine[name]["advantage"].append(row["vs_random"]["advantage"])
                    by_engine[name]["clear"].append(row["vs_random"]["advantage_ci"][0] > 0)
        summary[level] = {
            name: {
                "mean_final_regret": round(M.mean(v["regret"]), 4),
                **({"mean_advantage_over_random": round(M.mean(v["advantage"]), 4),
                    "benchmarks_clearly_ahead_of_random": f"{sum(v['clear'])}/{len(v['clear'])}"} if v["advantage"] else {}),
            }
            for name, v in by_engine.items()
        }
    return summary

"""Test functions for the optimisation evaluation: known optimum, all on the unit cube, all maximised.

Three are the standard Bayesian-optimisation benchmarks (Branin, Hartmann-3, Hartmann-6) so the numbers can be read against
the literature. The fourth is shaped like a formulation problem: three components whose amounts should add up to a whole
(a recipe is 100 %), with a different preferred level for each - a narrow ridge that an optimiser has to find, not a bump.

``f(x)`` is the true value; the evaluation adds measurement noise itself, because "regret" is always scored on the true value
of the point an optimiser would hand a chemist, while the optimiser only ever sees the noisy one.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Benchmark:
    name: str
    dim: int
    f: Callable[[np.ndarray], float]  # unit cube -> true value, to be maximised
    optimum: float
    description: str

    def reference_level(self, *, samples: int = 4000, seed: int = 12345) -> float:
        """Mean value of uniformly random points: the level a search that learned nothing sits at."""
        rng = np.random.default_rng(seed)
        return float(np.mean([self.f(rng.random(self.dim)) for _ in range(samples)]))

    def normalised_regret(self, best_value: float, reference: float) -> float:
        """0 at the optimum, 1 at the random-point average; negative never (the optimum bounds f)."""
        gap = self.optimum - reference
        return max(0.0, (self.optimum - best_value) / gap) if gap > 0 else 0.0


# ── Branin (2-D, three equal global optima) ──────────────────────────────────────────────────────


def _branin(u: np.ndarray) -> float:
    x1, x2 = -5.0 + 15.0 * u[0], 15.0 * u[1]
    a, b, c = 1.0, 5.1 / (4 * math.pi**2), 5.0 / math.pi
    r, s, t = 6.0, 10.0, 1.0 / (8 * math.pi)
    return -float(a * (x2 - b * x1**2 + c * x1 - r) ** 2 + s * (1 - t) * math.cos(x1) + s)


# ── Hartmann (3-D and 6-D) ───────────────────────────────────────────────────────────────────────

_H_ALPHA = np.array([1.0, 1.2, 3.0, 3.2])
_H3_A = np.array([[3.0, 10, 30], [0.1, 10, 35], [3.0, 10, 30], [0.1, 10, 35]])
_H3_P = 1e-4 * np.array([[3689, 1170, 2673], [4699, 4387, 7470], [1091, 8732, 5547], [381, 5743, 8828]])
_H6_A = np.array(
    [[10, 3, 17, 3.5, 1.7, 8], [0.05, 10, 17, 0.1, 8, 14], [3, 3.5, 1.7, 10, 17, 8], [17, 8, 0.05, 10, 0.1, 14]]
)
_H6_P = 1e-4 * np.array(
    [
        [1312, 1696, 5569, 124, 8283, 5886],
        [2329, 4135, 8307, 3736, 1004, 9991],
        [2348, 1451, 3522, 2883, 3047, 6650],
        [4047, 8828, 8732, 5743, 1091, 381],
    ]
)


def _hartmann(u: np.ndarray, a: np.ndarray, p: np.ndarray) -> float:
    return float(np.sum(_H_ALPHA * np.exp(-np.sum(a * (u - p) ** 2, axis=1))))


# ── a formulation-shaped ridge ───────────────────────────────────────────────────────────────────


def _ridge3(u: np.ndarray) -> float:
    """Amounts that should add up to one, each with a preferred level; the optimum (0.5, 0.3, 0.2) lies on the sum-to-one plane."""
    s = u[0] + u[1] + u[2] - 1.0
    return -float(20.0 * s**2 + (u[0] - 0.5) ** 2 + 2.0 * (u[1] - 0.3) ** 2 + (u[2] - 0.2) ** 2)


BENCHMARKS: tuple[Benchmark, ...] = (
    Benchmark("branin", 2, _branin, -0.397887, "2-D, three equal global optima, a curved valley"),
    Benchmark("hartmann3", 3, lambda u: _hartmann(u, _H3_A, _H3_P), 3.86278, "3-D, four local optima"),
    Benchmark("hartmann6", 6, lambda u: _hartmann(u, _H6_A, _H6_P), 3.32237, "6-D, six local optima"),
    Benchmark("ridge3", 3, _ridge3, 0.0, "three amounts that should sum to one, a narrow ridge (formulation-shaped)"),
)


def by_name(name: str) -> Benchmark:
    for benchmark in BENCHMARKS:
        if benchmark.name == name:
            return benchmark
    raise KeyError(name)

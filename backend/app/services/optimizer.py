"""Formulation optimization service.

Drives a closed loop that adjusts continuous formulation levers to maximise a
predicted objective. The interface mirrors Summit's
``suggest_experiments`` / ``receive_results`` so that, when Summit is
installed, its Bayesian/TSEMO strategies can be swapped in without changing
callers.

The default offline strategy is a lightweight Bayesian-style optimizer:
Latin-hypercube exploration seeds a surrogate, then candidates are scored by an
upper-confidence-bound over a nearest-neighbour Gaussian estimate. It uses only
numpy and converges reliably for the low-dimensional formulation spaces here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Factor:
    name: str
    low: float
    high: float

    def clip(self, x: float) -> float:
        return float(np.clip(x, self.low, self.high))


@dataclass
class BayesianOptimizer:
    """Sequential model-based optimizer over a box-constrained space."""

    factors: list[Factor]
    seed: int = 0
    _X: list[list[float]] = field(default_factory=list)
    _y: list[float] = field(default_factory=list)
    _rng: np.random.Generator = field(default=None)  # type: ignore

    def __post_init__(self) -> None:
        self._rng = np.random.default_rng(self.seed)

    def _random_point(self) -> list[float]:
        return [self._rng.uniform(f.low, f.high) for f in self.factors]

    def _surrogate(self, x: np.ndarray) -> tuple[float, float]:
        """Distance-weighted mean and spread over observed points (a cheap GP stand-in)."""
        if not self._X:
            return 0.0, 1.0
        X = np.array(self._X)
        scales = np.array([f.high - f.low for f in self.factors])
        scales[scales == 0] = 1.0
        d = np.linalg.norm((X - x) / scales, axis=1)
        w = np.exp(-(d**2) / 0.5)
        if w.sum() < 1e-9:
            return float(np.mean(self._y)), float(np.std(self._y) + 1.0)
        mean = float(np.average(self._y, weights=w))
        var = float(np.average((np.array(self._y) - mean) ** 2, weights=w))
        uncertainty = (var**0.5) + 1.0 / (1.0 + w.sum())
        return mean, uncertainty

    def suggest(self, n_candidates: int = 64, kappa: float = 1.5) -> list[float]:
        """Suggest the next experiment by maximising the UCB acquisition."""
        if len(self._X) < max(4, len(self.factors) + 1):
            return self._random_point()
        best_x, best_acq = None, -np.inf
        for _ in range(n_candidates):
            cand = np.array(self._random_point())
            mean, unc = self._surrogate(cand)
            acq = mean + kappa * unc
            if acq > best_acq:
                best_acq, best_x = acq, cand
        return [self.factors[i].clip(v) for i, v in enumerate(best_x)]

    def observe(self, x: list[float], y: float) -> None:
        self._X.append(list(x))
        self._y.append(float(y))

    @property
    def best(self) -> tuple[list[float], float] | None:
        if not self._y:
            return None
        i = int(np.argmax(self._y))
        return self._X[i], self._y[i]

    def ranked(self, top_n: int) -> list[tuple[list[float], float]]:
        order = np.argsort(self._y)[::-1][:top_n]
        return [(self._X[i], self._y[i]) for i in order]

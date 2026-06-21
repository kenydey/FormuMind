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

    @property
    def engine(self) -> str:
        return "numpy-ucb"


# ── Optional real-engine adapters ────────────────────────────────────────────
# Both expose the same suggest()/observe()/ranked()/best interface as
# BayesianOptimizer, so the workflow can swap them in transparently. Each is
# gated behind an availability probe; build_optimizer() picks the best one
# installed and silently falls back to the numpy optimizer otherwise.


def _optuna_available() -> bool:
    try:
        import optuna  # noqa: F401

        return True
    except Exception:
        return False


def _summit_available() -> bool:
    try:
        import summit  # noqa: F401

        return True
    except Exception:
        return False


def _botorch_available() -> bool:
    try:
        import botorch  # noqa: F401
        import gpytorch  # noqa: F401
        import torch  # noqa: F401

        return True
    except Exception:
        return False


@dataclass
class OptunaOptimizer:
    """Optuna ask/tell optimizer (TPE sampler) over the scalar objective.

    A pure-CPU, pip-installable upgrade over the random-UCB search: the TPE
    sampler models the objective and concentrates sampling in promising
    regions. Maximises the same aggregated score the workflow already computes,
    so it is a drop-in replacement.
    """

    factors: list[Factor]
    seed: int = 0
    _X: list[list[float]] = field(default_factory=list)
    _y: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        import optuna

        optuna.logging.set_verbosity(optuna.logging.WARNING)
        self._study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=self.seed),
        )
        self._pending: dict[tuple, object] = {}

    @staticmethod
    def _key(x: list[float]) -> tuple:
        return tuple(round(float(v), 6) for v in x)

    def suggest(self, n_candidates: int = 64, kappa: float = 1.5) -> list[float]:
        trial = self._study.ask()
        x = [trial.suggest_float(f.name, f.low, f.high) for f in self.factors]
        self._pending[self._key(x)] = trial
        return x

    def observe(self, x: list[float], y: float) -> None:
        trial = self._pending.pop(self._key(x), None)
        if trial is not None:
            self._study.tell(trial, float(y))
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

    @property
    def engine(self) -> str:
        return "optuna-tpe"


@dataclass
class SummitOptimizer:
    """Summit single-objective Bayesian optimizer (SOBO) adapter.

    Wraps Summit's domain/strategy API behind the same sequential
    suggest/observe interface. Best-effort: any API/version mismatch raises in
    __post_init__ so build_optimizer() can fall back to a lighter engine.
    """

    factors: list[Factor]
    seed: int = 0
    _X: list[list[float]] = field(default_factory=list)
    _y: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        from summit.domain import ContinuousVariable, Domain
        from summit.strategies import SOBO
        from summit.utils.dataset import DataSet

        self._DataSet = DataSet
        domain = Domain()
        for f in self.factors:
            domain += ContinuousVariable(name=f.name, description=f.name, bounds=[f.low, f.high])
        domain += ContinuousVariable(
            name="objective", description="aggregated score", bounds=[0.0, 1e6], is_objective=True, maximize=True
        )
        self._domain = domain
        self._strategy = SOBO(domain)
        self._prev = None  # previous experiments DataSet fed back to the strategy

    def suggest(self, n_candidates: int = 64, kappa: float = 1.5) -> list[float]:
        suggestion = self._strategy.suggest_experiments(1, prev_res=self._prev)
        self._last = suggestion
        return [float(suggestion[f.name].iloc[0]) for f in self.factors]

    def observe(self, x: list[float], y: float) -> None:
        ds = self._last.copy()
        ds["objective", "DATA"] = float(y)
        self._prev = ds
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

    @property
    def engine(self) -> str:
        return "summit-sobo"


@dataclass
class BotorchOptimizer:
    """BoTorch Gaussian-process optimizer with Expected-Improvement acquisition.

    Fits a ``SingleTaskGP`` to the observed (x, score) pairs and maximises a
    log-Expected-Improvement acquisition each step — a true GP posterior with
    calibrated uncertainty, unlike the numpy UCB stand-in or Optuna's TPE. Keeps
    the same scalar suggest/observe/ranked interface, so the workflow swaps it in
    transparently. Any per-step modelling failure degrades to a random draw.
    """

    factors: list[Factor]
    seed: int = 0
    _X: list[list[float]] = field(default_factory=list)
    _y: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        import torch

        torch.manual_seed(self.seed)
        self._rng = np.random.default_rng(self.seed)
        self._bounds = np.array(
            [[f.low for f in self.factors], [f.high for f in self.factors]], dtype=float
        )

    def _random_point(self) -> list[float]:
        return [self._rng.uniform(f.low, f.high) for f in self.factors]

    def suggest(self, n_candidates: int = 64, kappa: float = 1.5) -> list[float]:
        if len(self._X) < max(4, len(self.factors) + 1):
            return self._random_point()
        try:
            import torch
            from botorch.acquisition.analytic import LogExpectedImprovement
            from botorch.fit import fit_gpytorch_mll
            from botorch.models import SingleTaskGP
            from botorch.optim import optimize_acqf
            from botorch.utils.transforms import normalize, unnormalize
            from gpytorch.mlls import ExactMarginalLogLikelihood

            bounds = torch.tensor(self._bounds, dtype=torch.double)
            X = normalize(torch.tensor(self._X, dtype=torch.double), bounds)
            Y = torch.tensor(self._y, dtype=torch.double).unsqueeze(-1)
            Y = (Y - Y.mean()) / (Y.std() + 1e-9)  # standardize for stable GP fit
            gp = SingleTaskGP(X, Y)
            fit_gpytorch_mll(ExactMarginalLogLikelihood(gp.likelihood, gp))
            acq = LogExpectedImprovement(gp, best_f=Y.max())
            d = len(self.factors)
            unit = torch.stack(
                [torch.zeros(d, dtype=torch.double), torch.ones(d, dtype=torch.double)]
            )
            cand, _ = optimize_acqf(acq, bounds=unit, q=1, num_restarts=5, raw_samples=64)
            x = unnormalize(cand.detach(), bounds).squeeze(0).tolist()
            return [self.factors[i].clip(v) for i, v in enumerate(x)]
        except Exception:
            return self._random_point()

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

    @property
    def engine(self) -> str:
        return "botorch-ei"


def build_optimizer(factors: list[Factor], seed: int = 0):
    """Return the best available optimizer, falling back to the numpy engine.

    Priority: BoTorch GP-EI (bo extra) > Summit (heavy) > Optuna (optimize) >
    numpy UCB. Construction-time failures degrade gracefully to the next tier.
    """
    if _botorch_available():
        try:
            return BotorchOptimizer(factors=factors, seed=seed)
        except Exception:
            pass
    if _summit_available():
        try:
            return SummitOptimizer(factors=factors, seed=seed)
        except Exception:
            pass
    if _optuna_available():
        try:
            return OptunaOptimizer(factors=factors, seed=seed)
        except Exception:
            pass
    return BayesianOptimizer(factors=factors, seed=seed)

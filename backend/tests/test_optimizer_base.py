"""Regression tests for the optimizer ``best``/``ranked`` dedup (2026-09-28).

The four optimizer adapters (Bayesian/Optuna/Summit/BoTorch) carried
character-identical copies of ``best``/``ranked``; they now inherit both from
``_ObservedHistoryMixin``. These tests pin the pre-merge behaviour: same
shared implementation on every adapter, and identical semantics.
"""

import numpy as np

from app.services.optimizer import (
    BayesianOptimizer,
    BotorchOptimizer,
    Factor,
    OptunaOptimizer,
    SummitOptimizer,
    _ObservedHistoryMixin,
)

_ADAPTERS = (BayesianOptimizer, OptunaOptimizer, SummitOptimizer, BotorchOptimizer)


def test_all_adapters_share_mixin_implementation():
    """Every adapter resolves best/ranked to the mixin — no per-class copies."""
    for cls in _ADAPTERS:
        assert issubclass(cls, _ObservedHistoryMixin), cls.__name__
        assert cls.best.fget is _ObservedHistoryMixin.best.fget, cls.__name__
        assert cls.ranked is _ObservedHistoryMixin.ranked, cls.__name__


def _factors():
    return [Factor("a", 0.0, 1.0), Factor("b", 0.0, 1.0)]


def test_best_empty_and_argmax():
    opt = BayesianOptimizer(factors=_factors(), seed=0)
    assert opt.best is None
    opt.observe([0.1, 0.2], 1.0)
    opt.observe([0.3, 0.4], 3.0)
    opt.observe([0.5, 0.6], 2.0)
    x, y = opt.best
    assert x == [0.3, 0.4] and y == 3.0


def test_ranked_descending_top_n():
    opt = BayesianOptimizer(factors=_factors(), seed=0)
    for i in range(5):
        opt.observe([i / 10.0, 0.0], float(i))
    ranked = opt.ranked(3)
    assert [y for _, y in ranked] == [4.0, 3.0, 2.0]
    assert all(isinstance(x, list) for x, _ in ranked)


def test_ranked_top_n_larger_than_history():
    opt = BayesianOptimizer(factors=_factors(), seed=0)
    opt.observe([0.1, 0.1], 1.5)
    ranked = opt.ranked(10)
    assert len(ranked) == 1 and ranked[0][1] == 1.5


def test_mixin_matches_numpy_reference():
    """best/ranked agree with a direct numpy argmax/argsort reference."""
    rng = np.random.default_rng(7)
    opt = BayesianOptimizer(factors=_factors(), seed=0)
    ys = rng.uniform(0, 10, size=12)
    for i, y in enumerate(ys):
        opt.observe([i / 100.0, 0.0], float(y))
    bx, by = opt.best
    i = int(np.argmax(ys))
    assert by == float(ys[i]) and bx == [i / 100.0, 0.0]
    order = np.argsort(ys)[::-1][:5]
    assert [y for _, y in opt.ranked(5)] == [float(ys[j]) for j in order]

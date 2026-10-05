"""P1-2 golden: DOE design correctness gate.

Asserts the mathematical contracts of the design generators:
run-count formulas, factor-bound satisfaction, mixture sums, and an
LHS space-filling lower bound. Deterministic; no LLM, no KB.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.domain import doe as doe_engine
from app.domain.schemas import DOEFactor


def _factors(k: int, low: float = 0.0, high: float = 10.0) -> list[DOEFactor]:
    return [DOEFactor(name=f"x{i}", low=low, high=high) for i in range(k)]


def test_golden_full_factorial_run_counts():
    for k, expected in [(2, 4), (3, 8), (4, 16)]:
        plan = doe_engine.build_plan(_factors(k), design="full_factorial")
        assert len(plan.runs) == expected, (k, len(plan.runs))


def test_golden_fractional_factorial_run_counts():
    # k<=3 falls back to full factorial; otherwise 2^(k-1) half-fraction.
    assert len(doe_engine.build_plan(_factors(3), design="fractional_factorial").runs) == 8
    assert len(doe_engine.build_plan(_factors(4), design="fractional_factorial").runs) == 8
    assert len(doe_engine.build_plan(_factors(5), design="fractional_factorial").runs) == 16


def test_golden_ccd_run_counts():
    # factorial + 2k axial + 3 centre; factorial branch is 2^k for k<=4,
    # 2^(k-1) half-fraction beyond.
    assert len(doe_engine.build_plan(_factors(2), design="ccd").runs) == 4 + 4 + 3
    assert len(doe_engine.build_plan(_factors(3), design="ccd").runs) == 8 + 6 + 3
    assert len(doe_engine.build_plan(_factors(5), design="ccd").runs) == 16 + 10 + 3


def test_golden_plackett_burman_run_count():
    # next multiple of 4 with (m - 1) >= k columns.
    plan = doe_engine.build_plan(_factors(5), design="plackett_burman")
    assert len(plan.runs) == 8


def test_golden_bounds_respected_all_designs():
    """Every run's natural value must lie within [low, high]: 100%.

    CCD is excluded on purpose: its rotatable axial points extend beyond
    the factorial box by design (see test_golden_ccd_structure).
    """
    for design in ("full_factorial", "fractional_factorial", "plackett_burman", "lhs"):
        factors = _factors(3, low=1.0, high=9.0)
        plan = doe_engine.build_plan(factors, design=design, n=12)
        assert plan.runs, design
        for run in plan.runs:
            for f in factors:
                v = run.natural[f.name]
                assert f.low - 1e-9 <= v <= f.high + 1e-9, (design, f.name, v)


def test_golden_ccd_structure():
    """CCD contract: factorial block inside bounds, axial block at +/-alpha.

    Rotatable alpha = (n_factorial)^0.25 > 1, so axial points legitimately
    fall outside [low, high]; the gate pins this documented behaviour.
    """
    k = 3
    factors = _factors(k, low=1.0, high=9.0)
    plan = doe_engine.build_plan(factors, design="ccd")
    alpha = float(2**k) ** 0.25
    assert alpha > 1.0
    axial = 0
    for run in plan.runs:
        coded = [run.coded[f.name] for f in factors]
        extreme = [c for c in coded if abs(c) > 1.0 + 1e-9]
        if extreme:
            # axial point: exactly one factor at +/-alpha, rest at 0
            axial += 1
            assert len(extreme) == 1
            assert abs(abs(extreme[0]) - alpha) < 1e-3  # coded rounded to 4dp
            assert all(abs(c) < 1e-9 for c in coded if abs(c) <= 1.0 + 1e-9)
        else:
            # factorial / centre block: inside bounds
            for f in factors:
                v = run.natural[f.name]
                assert f.low - 1e-9 <= v <= f.high + 1e-9, (f.name, v)
    assert axial == 2 * k


def test_golden_lhs_space_filling_lower_bound():
    """Seeded LHS min pairwise distance beats seeded uniform random."""
    k, n, seed = 3, 24, 7
    lhs = doe_engine.latin_hypercube(k, n, seed=seed)
    rng = np.random.default_rng(seed)
    rand = rng.random((n, k))

    def min_dist(m):
        d = np.linalg.norm(m[:, None, :] - m[None, :, :], axis=-1)
        iu = np.triu_indices(n, k=1)
        return float(d[iu].min())

    assert min_dist(lhs) > min_dist(rand)


def test_golden_simplex_lattice_mixture_sums():
    """pydoe simplex_lattice: every run keeps the components' baseline total and stays in range.

    Contract (doe_adapter._mixture_row_to_run): the components share ``Σ midpoint`` (the mass the
    baseline recipe gives them), each value inside its own ``[low, high]``. (The earlier contract —
    shares of ``Σ high`` — put runs outside every declared bound.)
    """
    pytest.importorskip("pydoe", reason="pydoe engine is optional")
    from app.services.engines.pydoe_engine import build_pydoe_plan

    factors = _factors(3)
    plan = build_pydoe_plan(factors, design="simplex_lattice")
    assert plan.runs
    total = sum((f.low + f.high) / 2 for f in factors)
    for run in plan.runs:
        s = sum(run.natural[f.name] for f in factors)
        assert abs(s - total) < 1e-3, (run.natural, s)
        assert all(f.low - 1e-9 <= run.natural[f.name] <= f.high + 1e-9 for f in factors)


def _run_matrix(plan, factors):
    return np.array([[run.natural[f.name] for f in factors] for run in plan.runs])


def test_golden_pydoe_lhs_seed_reproducible():
    """A-1: pydoe lhs with an explicit seed must be byte-identical across calls.

    Regression for the pydoe>=1.0 OS-entropy default (pydoe_engine passed no
    seed, so every LHS plan differed run to run).
    """
    pytest.importorskip("pydoe", reason="pydoe engine is optional")
    from app.services.engines.pydoe_engine import build_pydoe_plan

    factors = _factors(3)
    p1 = build_pydoe_plan(factors, design="lhs", n=8, seed=42)
    p2 = build_pydoe_plan(factors, design="lhs", n=8, seed=42)
    assert np.array_equal(_run_matrix(p1, factors), _run_matrix(p2, factors))

    # Different seeds must (with overwhelming probability) differ.
    p3 = build_pydoe_plan(factors, design="lhs", n=8, seed=43)
    assert not np.array_equal(_run_matrix(p1, factors), _run_matrix(p3, factors))

    # seed=None preserves the old non-seeded call path (no crash, valid plan).
    p4 = build_pydoe_plan(factors, design="lhs", n=8)
    assert len(p4.runs) == 8


def test_golden_doe_registry_seed_passthrough():
    """A-1: seed threads through the registry to the pydoe engine."""
    pytest.importorskip("pydoe", reason="pydoe engine is optional")
    from app.services.engines.doe_registry import build_doe_plan

    factors = _factors(3)
    p1 = build_doe_plan(factors, "lhs", engine="pydoe", n=8, seed=7)
    p2 = build_doe_plan(factors, "lhs", engine="pydoe", n=8, seed=7)
    assert np.array_equal(_run_matrix(p1, factors), _run_matrix(p2, factors))

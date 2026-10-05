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

    CCD is included: by default its axial points sit on the faces of the factorial box (alpha = 1). The rotatable
    variant, whose star points leave the box, is opt-in and flags those runs (see test_golden_ccd_structure).
    """
    for design in ("full_factorial", "fractional_factorial", "plackett_burman", "ccd", "lhs"):
        factors = _factors(3, low=1.0, high=9.0)
        plan = doe_engine.build_plan(factors, design=design, n=12)
        assert plan.runs, design
        for run in plan.runs:
            for f in factors:
                v = run.natural[f.name]
                assert f.low - 1e-9 <= v <= f.high + 1e-9, (design, f.name, v)


def _axial_runs(plan, factors, alpha):
    """Runs with exactly one factor at +/-alpha and the rest at 0 - the star block of a CCD."""
    star = []
    for run in plan.runs:
        coded = [run.coded[f.name] for f in factors]
        at_alpha = [c for c in coded if abs(abs(c) - alpha) < 1e-3]  # coded is rounded to 4dp
        rest_zero = all(abs(c) < 1e-9 for c in coded if abs(abs(c) - alpha) >= 1e-3)
        if len(at_alpha) == 1 and rest_zero:
            star.append(run)
    return star


def test_golden_ccd_structure():
    """CCD contract, default: factorial block at the corners, axial block on the faces (alpha = 1), centre points.

    Every run is inside [low, high] and none is marked infeasible - the earlier default (rotatable alpha, star points
    at +/-1.68 for 3 factors) put runs at negative concentrations and only said so in a note.
    """
    k = 3
    factors = _factors(k, low=1.0, high=9.0)
    plan = doe_engine.build_plan(factors, design="ccd")
    assert len(_axial_runs(plan, factors, 1.0)) >= 2 * k  # the faces (the factorial corners have no zero coordinate)
    centre = [r for r in plan.runs if all(abs(v) < 1e-9 for v in r.coded.values())]
    assert len(centre) == 3
    for run in plan.runs:
        assert not run.infeasible, (run.run_id, run.infeasible_reason)
        for f in factors:
            assert f.low - 1e-9 <= run.natural[f.name] <= f.high + 1e-9, (f.name, run.natural[f.name])
    assert len(plan.runs) == 2**k + 2 * k + 3
    assert "alpha=1" in plan.notes and "face-centred" in plan.notes and "WARNING" not in plan.notes


def test_golden_ccd_rotatable_keeps_star_points_outside_and_flags_them():
    """Opt-in rotatable CCD: axial block at +/-(n_factorial)^0.25 > 1, outside [low, high], marked infeasible."""
    k = 3
    factors = _factors(k, low=1.0, high=9.0)
    plan = doe_engine.build_plan(factors, design="ccd", ccd_alpha="rotatable")
    alpha = float(2**k) ** 0.25
    assert alpha > 1.0
    star = _axial_runs(plan, factors, alpha)
    assert len(star) == 2 * k
    assert {r.run_id for r in star} == {r.run_id for r in plan.runs if r.infeasible}
    for run in star:
        assert "星点超出因子范围" in (run.infeasible_reason or "")
    for run in plan.runs:
        if not run.infeasible:  # factorial / centre block stays inside the box
            for f in factors:
                assert f.low - 1e-9 <= run.natural[f.name] <= f.high + 1e-9
    assert f"{2 * k} of {len(plan.runs)} runs" in plan.notes and "marked infeasible" in plan.notes


def test_golden_ccd_numeric_alpha_and_invalid_alpha():
    factors = _factors(2, low=0.0, high=10.0)
    plan = doe_engine.build_plan(factors, design="ccd", ccd_alpha=1.5)
    assert max(abs(v) for r in plan.runs for v in r.coded.values()) == pytest.approx(1.5)
    assert sum(1 for r in plan.runs if r.infeasible) == 4
    assert doe_engine.build_plan(factors, design="ccd", ccd_alpha="1").notes  # a numeric string is a number
    for bad in ("nope", 0, -1, float("nan"), float("inf"), True, [1]):
        with pytest.raises(ValueError, match="ccd_alpha"):
            doe_engine.build_plan(factors, design="ccd", ccd_alpha=bad)


def test_ccd_alpha_is_ignored_by_other_designs():
    factors = _factors(2)
    assert len(doe_engine.build_plan(factors, design="full_factorial", ccd_alpha="nope").runs) == 4


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

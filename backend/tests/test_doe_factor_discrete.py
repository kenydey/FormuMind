"""C-4a: discrete DOE factors — schema validation, native decode, engine branches.

- DOEFactor / FactorCandidate kind+levels validation
- native ``decode`` maps coded [-1, 1] onto the level index
- BayBE maps discrete factors to NumericalDiscreteParameter / CategoricalParameter
- pydoe fails closed on discrete factors
"""
from __future__ import annotations

import pytest

from app.domain.doe import build_plan, decode
from app.domain.schemas import DOEFactor, FactorCandidate


def _discrete_num() -> DOEFactor:
    return DOEFactor(
        name="temp", low=100, high=200, kind="discrete",
        levels=[100.0, 150.0, 200.0],
    )


def _discrete_str() -> DOEFactor:
    return DOEFactor(name="solvent", low=0, high=1, kind="discrete",
                     levels=["A", "B", "C"])


# ── schema validation ──────────────────────────────────────────────────────


def test_discrete_requires_levels() -> None:
    with pytest.raises(Exception):
        DOEFactor(name="t", low=0, high=1, kind="discrete")


def test_discrete_requires_at_least_two_levels() -> None:
    with pytest.raises(Exception):
        DOEFactor(name="t", low=0, high=1, kind="discrete", levels=[1.0])


def test_unknown_kind_rejected() -> None:
    with pytest.raises(Exception):
        DOEFactor(name="t", low=0, high=1, kind="categorical", levels=["a", "b"])


def test_continuous_default_ignores_levels() -> None:
    f = DOEFactor(name="x", low=0, high=10)
    assert f.kind == "continuous"
    assert f.levels is None
    # continuous with stray levels does not raise (ignored by every engine)
    f2 = DOEFactor(name="x", low=0, high=10, levels=[1.0, 2.0])
    assert f2.kind == "continuous"


def test_factor_candidate_discrete_validation() -> None:
    with pytest.raises(Exception):
        FactorCandidate(name="t", low=0, high=1, kind="discrete", levels=[])
    c = FactorCandidate(name="t", low=0, high=1, kind="discrete", levels=["a", "b"])
    assert c.kind == "discrete"


# ── native decode ────────────────────────────────────────────────────────────


def test_decode_discrete_numeric_endpoints() -> None:
    f = _discrete_num()
    assert decode(-1.0, f) == 100.0
    assert decode(1.0, f) == 200.0
    assert decode(0.0, f) == 150.0


def test_decode_discrete_string_levels() -> None:
    f = _discrete_str()
    assert decode(-1.0, f) == "A"
    assert decode(1.0, f) == "C"


def test_decode_discrete_clamps_out_of_range() -> None:
    f = _discrete_num()
    assert decode(-2.0, f) == 100.0
    assert decode(2.0, f) == 200.0


def test_decode_continuous_unchanged() -> None:
    f = DOEFactor(name="x", low=0, high=10)
    assert decode(0.0, f) == 5.0
    assert decode(-1.0, f) == 0.0
    assert decode(1.0, f) == 10.0


def test_native_build_plan_with_discrete_factor() -> None:
    # C-4a: mixed-level full factorial — the 3-level discrete factor crosses
    # the 2-level continuous factor, so all 3 levels appear in the runs.
    plan = build_plan(
        [_discrete_str(), DOEFactor(name="y", low=0, high=1)],
        design="full_factorial",
    )
    assert len(plan.runs) == 6
    solvents = {r.natural["solvent"] for r in plan.runs}
    assert solvents == {"A", "B", "C"}
    # Continuous factor still spans its endpoints.
    assert {r.natural["y"] for r in plan.runs} == {0.0, 1.0}


def test_full_factorial_mixed_level_counts() -> None:
    from app.domain.doe import full_factorial

    grid = full_factorial(2, levels=[3, 2])
    assert grid.shape == (6, 2)
    # First column hits all three coded levels; second stays binary.
    assert sorted(set(grid[:, 0].round(4))) == [-1.0, 0.0, 1.0]
    assert sorted(set(grid[:, 1].round(4))) == [-1.0, 1.0]


def test_full_factorial_all_continuous_unchanged() -> None:
    from app.domain.doe import full_factorial

    grid = full_factorial(2)
    assert grid.shape == (4, 2)
    with pytest.raises(ValueError, match="level counts"):
        full_factorial(2, levels=[3])


# ── BayBE branch ─────────────────────────────────────────────────────────────


def test_baybe_discrete_parameter_types() -> None:
    baybe = pytest.importorskip("baybe")
    from app.domain.schemas import Requirement
    from app.services.engines.adapters.baybe_space_builder import build_searchspace

    req = Requirement(title="t", description="d", domain="surface_treatment")
    factors = [
        _discrete_num(),
        _discrete_str(),
        DOEFactor(name="conc", low=1, high=5, unit="wt%"),
    ]
    ss = build_searchspace(req, factors)
    by_name = {p.name: p for p in ss.parameters}
    assert type(by_name["temp"]).__name__ == "NumericalDiscreteParameter"
    assert type(by_name["solvent"]).__name__ == "CategoricalParameter"
    assert type(by_name["conc"]).__name__ == "NumericalContinuousParameter"
    assert baybe is not None


def test_baybe_discrete_mixed_levels_fail_closed() -> None:
    pytest.importorskip("baybe")
    from app.services.engines.adapters.baybe_space_builder import _discrete_parameter

    f = DOEFactor.model_construct(
        name="m", low=0, high=1, kind="discrete", levels=[1.0, "x"]
    )
    with pytest.raises(ValueError, match="mixed-type"):
        _discrete_parameter(f)


def test_baybe_wt_constraint_rhs_excludes_discrete() -> None:
    """C-4a: _max_lever_sum must not count discrete wt% highs into the RHS.

    Discrete factors cannot participate in ContinuousLinearConstraint (this
    BayBE version rejects discrete parameters), so including their ``high``
    would only loosen the bound.
    """
    pytest.importorskip("baybe")
    from app.domain.schemas import Requirement
    from app.services.engines.adapters.baybe_space_builder import (
        _max_lever_sum,
        build_searchspace,
    )

    req = Requirement(title="t", description="d", domain="surface_treatment")
    factors = [
        DOEFactor(name="resin", low=5, high=60, unit="wt%"),
        DOEFactor(name="hardener", low=5, high=60, unit="wt%"),
        # Discrete wt%: high=30 must NOT inflate the RHS.
        DOEFactor(name="pigment", low=0, high=30, unit="wt%",
                  kind="discrete", levels=[10.0, 20.0, 30.0]),
    ]
    assert _max_lever_sum(factors) == 100.0  # min(100, 60+60), not min(100, 150)
    ss = build_searchspace(req, factors)
    assert len(ss.constraints) == 1
    constraint = ss.constraints[0]
    assert sorted(constraint.parameters) == ["hardener", "resin"]
    assert float(constraint.rhs) == 100.0


# ── pydoe fail-closed ────────────────────────────────────────────────────────


def test_pydoe_discrete_fails_closed() -> None:
    pytest.importorskip("pydoe")
    from app.services.engines.pydoe_engine import build_pydoe_plan

    with pytest.raises(ValueError, match="does not support discrete factors"):
        build_pydoe_plan(
            [_discrete_num(), DOEFactor(name="y", low=0, high=1)],
            design="lhs",
            n=4,
            seed=1,
        )


def test_pydoe_continuous_still_works() -> None:
    pytest.importorskip("pydoe")
    from app.services.engines.pydoe_engine import build_pydoe_plan

    plan = build_pydoe_plan(
        [DOEFactor(name="x", low=0, high=1), DOEFactor(name="y", low=0, high=1)],
        design="lhs",
        n=4,
        seed=1,
    )
    assert len(plan.runs) == 4

from app.domain import doe
from app.domain.schemas import DOEFactor


FACTORS = [
    DOEFactor(name="A", low=2.0, high=14.0, unit="wt%"),
    DOEFactor(name="B", low=28.0, high=48.0, unit="wt%"),
    DOEFactor(name="C", low=8.0, high=22.0, unit="wt%"),
]


def test_full_factorial_run_count():
    plan = doe.build_plan(FACTORS, design="full_factorial")
    assert plan.design == "full_factorial"
    assert len(plan.runs) == 2 ** len(FACTORS)


def test_decode_maps_coded_to_natural_bounds():
    f = DOEFactor(name="A", low=2.0, high=14.0)
    assert doe.decode(-1.0, f) == 2.0
    assert doe.decode(1.0, f) == 14.0
    assert doe.decode(0.0, f) == 8.0


def test_plackett_burman_screening_size():
    plan = doe.build_plan(FACTORS, design="plackett_burman")
    # Next multiple of 4 with >= k factors is 8 runs.
    assert len(plan.runs) == 8


def test_ccd_has_centre_and_axial_points():
    plan = doe.build_plan(FACTORS, design="ccd")
    # factorial(8) + 2*k axial(6) + 3 centre = 17
    assert len(plan.runs) == 8 + 2 * len(FACTORS) + 3
    centre = [r for r in plan.runs if all(v == 0.0 for v in r.coded.values())]
    assert len(centre) == 3


def test_lhs_run_count_and_bounds():
    plan = doe.build_plan(FACTORS, design="lhs", n=10)
    assert len(plan.runs) == 10
    for run in plan.runs:
        for f in FACTORS:
            assert f.low - 1e-6 <= run.natural[f.name] <= f.high + 1e-6


def test_unknown_design_raises():
    import pytest

    with pytest.raises(ValueError):
        doe.build_plan(FACTORS, design="nope")

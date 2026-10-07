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


def _lhs_factors():
    from app.domain.schemas import DOEFactor

    return [
        DOEFactor(name="x", kind="continuous", low=0.0, high=10.0, unit=""),
        DOEFactor(name="y", kind="continuous", low=0.0, high=10.0, unit=""),
    ]


def test_v15_native_lhs_seed_reproducible():
    """v15: native 引擎 LHS 透传 seed——同 seed 相同、异 seed 不同。"""
    from app.services.engines.doe_registry import build_doe_plan

    fs = _lhs_factors()
    key = lambda p: [tuple(r.natural.values()) for r in p.runs]  # noqa: E731
    p1 = build_doe_plan(fs, "lhs", engine="native", n=8, seed=123)
    p2 = build_doe_plan(fs, "lhs", engine="native", n=8, seed=456)
    p3 = build_doe_plan(fs, "lhs", engine="native", n=8, seed=123)
    assert key(p1) != key(p2), "不同 seed 应生成不同方案"
    assert key(p1) == key(p3), "相同 seed 应生成相同方案"


def test_v15_pydoe_fallback_keeps_seed():
    """v15: pydoe→native fallback 不丢 seed（monckeypatch 强制 fallback）。"""
    import app.services.engines.pydoe_engine as pe
    from app.services.engines.doe_registry import build_doe_plan

    fs = _lhs_factors()
    key = lambda p: [tuple(r.natural.values()) for r in p.runs]  # noqa: E731
    real = pe.build_pydoe_plan

    def boom(*a, **k):
        raise RuntimeError("forced fallback")

    pe.build_pydoe_plan = boom
    try:
        p1 = build_doe_plan(fs, "lhs", engine="pydoe", n=8, seed=123)
        p2 = build_doe_plan(fs, "lhs", engine="pydoe", n=8, seed=123)
        p3 = build_doe_plan(fs, "lhs", engine="pydoe", n=8, seed=456)
    finally:
        pe.build_pydoe_plan = real
    assert "fallback" in p1.notes
    assert key(p1) == key(p2), "fallback 后同 seed 应相同"
    assert key(p1) != key(p3), "fallback 后异 seed 应不同"

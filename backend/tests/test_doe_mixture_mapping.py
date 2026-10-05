"""Mixture designs on a real recipe: components add up, nothing leaves its range, process factors stay put (round-4).

The simplex-lattice mapping treated *every* factor as a mixture component and scaled the proportions by
``Σ high`` over all of them — including the cure temperature. On the default anticorrosion requirement the
first run was ``epoxy 156 wt%, hardener 0, zinc phosphate 0, cure 0 °C``: every run outside every declared
bound, with no warning. The UI offers this design ("混合物 simplex").
"""
from __future__ import annotations

import numpy as np
import pytest

from app.domain.schemas import DOEFactor
from app.services.engines.adapters.doe_adapter import (
    _fit_to_bounds,
    matrix_to_doe_plan,
    split_mixture_factors,
)

# the shape the default anticorrosion requirement produces
RECIPE = [
    DOEFactor(name="Bisphenol-A epoxy (DGEBA)", low=26.6, high=49.4, unit="wt%"),
    DOEFactor(name="Polyamide hardener", low=9.8, high=18.2, unit="wt%"),
    DOEFactor(name="Zinc phosphate", low=4.55, high=8.45, unit="wt%"),
    DOEFactor(name="cure_temperature_c", low=50.0, high=80.0, unit="C"),
]
COMPONENTS = RECIPE[:3]


def _lattice(q: int = 3) -> np.ndarray:
    """Proportion rows like pydoe's simplex_lattice_design(q, 2)."""
    rows = [[1.0 if i == j else 0.0 for j in range(q)] for i in range(q)]
    rows += [[0.5 if k in (i, j) else 0.0 for k in range(q)] for i in range(q) for j in range(i + 1, q)]
    return np.array(rows)


def test_process_factors_are_not_mixture_components():
    components, process = split_mixture_factors(RECIPE)
    assert components == [0, 1, 2] and process == [3]
    assert split_mixture_factors([DOEFactor(name="bath", low=1, high=2, unit="g/L")]) == ([], [0])
    assert split_mixture_factors([DOEFactor(name="x", low=0, high=1)]) == ([0], [])  # unit-less = a share


def test_every_run_stays_inside_every_range_and_keeps_the_total():
    plan = matrix_to_doe_plan(_lattice(3), RECIPE, "simplex_lattice", engine="test")
    total = sum((f.low + f.high) / 2 for f in COMPONENTS)
    assert len(plan.runs) == 6
    for run in plan.runs:
        assert sum(run.natural[f.name] for f in COMPONENTS) == pytest.approx(total, abs=1e-3)
        for f in RECIPE:
            assert f.low - 1e-9 <= run.natural[f.name] <= f.high + 1e-9, (f.name, run.natural)
        assert run.natural["cure_temperature_c"] == pytest.approx(65.0)  # held at the midpoint
        assert all(-1.0 - 1e-9 <= v <= 1.0 + 1e-9 for v in run.coded.values())
    assert "cure_temperature_c" in plan.notes and "midpoint" in plan.notes


def test_a_vertex_that_would_break_a_small_components_range_is_pulled_back_not_dropped():
    """Zinc phosphate may only vary 4.55–8.45, so the "all zinc phosphate" vertex cannot be 100% of anything."""
    plan = matrix_to_doe_plan(_lattice(3), RECIPE, "simplex_lattice", engine="test")
    zinc_vertex = max(plan.runs, key=lambda r: r.natural["Zinc phosphate"])
    assert zinc_vertex.natural["Zinc phosphate"] == pytest.approx(8.45)
    assert sum(zinc_vertex.natural[f.name] for f in COMPONENTS) == pytest.approx(58.5, abs=1e-3)


def test_the_projection_is_a_noop_for_points_already_inside():
    low, high = np.array([0.0, 0.0]), np.array([10.0, 10.0])
    assert _fit_to_bounds(np.array([4.0, 6.0]), low, high, 10.0).tolist() == [4.0, 6.0]
    out = _fit_to_bounds(np.array([14.0, -4.0]), low, high, 10.0)  # outside, same sum
    assert out.sum() == pytest.approx(10.0) and (out >= low).all() and (out <= high).all()


def test_the_matrix_has_one_column_per_component_not_per_factor():
    with pytest.raises(ValueError, match="mixture components"):
        matrix_to_doe_plan(_lattice(4), RECIPE, "simplex_lattice", engine="test")


def test_a_design_with_fewer_than_two_components_is_refused():
    pytest.importorskip("pydoe")
    from app.services.engines.pydoe_engine import build_pydoe_plan

    only_process = [DOEFactor(name="cure_temperature_c", low=50, high=80, unit="C"), DOEFactor(name="bath", low=1, high=2, unit="g/L")]
    with pytest.raises(ValueError, match="混料设计"):
        build_pydoe_plan(only_process, design="simplex_lattice")


def test_the_api_serves_a_usable_mixture_plan_for_the_default_recipe(tmp_path, monkeypatch):
    pytest.importorskip("pydoe")
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.domain.project_workspace import default_requirement
    from app.main import app

    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{tmp_path}/doe.db")
    get_settings.cache_clear()
    from app.db.database import Base, default_engine

    Base.metadata.create_all(default_engine())
    try:
        r = TestClient(app).post(
            "/api/doe", params={"design": "simplex_lattice", "engine": "pydoe"},
            json=default_requirement().model_dump(mode="json"),
        )
    finally:
        get_settings.cache_clear()
    assert r.status_code == 200, r.text
    plan = r.json()
    bounds = {f["name"]: (f["low"], f["high"]) for f in plan["factors"]}
    assert plan["runs"]
    for run in plan["runs"]:
        for name, value in run["natural"].items():
            lo, hi = bounds[name]
            assert lo - 1e-6 <= value <= hi + 1e-6, (name, value, lo, hi)

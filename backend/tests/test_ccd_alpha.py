"""``ccd_alpha`` through the engines and the API: face-centred by default, rotatable on request, flagged not clipped.

Round 4 left this open (plan #3): the native CCD put its star points at +/-(n_factorial)^0.25 - 1.68 coded units for
three factors, i.e. negative concentrations - and only said so in a note, while the pyDOE engine clipped the same points
onto the faces. The decision: face-centred (alpha = 1) is the default everywhere, a rotatable (or explicit-alpha) CCD is
available on request and keeps its star points outside the box, marked ``infeasible`` rather than clipped.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain.schemas import DOEFactor
from app.main import app
from app.services.engines.doe_registry import build_doe_plan, pydoe_available

FACTORS = [
    DOEFactor(name="A", low=1.0, high=10.0, unit="wt%"),
    DOEFactor(name="B", low=2.0, high=8.0, unit="wt%"),
    DOEFactor(name="C", low=20.0, high=60.0, unit="°C"),
]


def _inside(plan) -> bool:
    return all(
        f.low - 1e-9 <= run.natural[f.name] <= f.high + 1e-9 for run in plan.runs for f in plan.factors
    )


@pytest.mark.parametrize("engine", ["native", "auto", "pydoe"])
def test_every_engine_gives_a_face_centred_ccd_by_default(engine):
    plan = build_doe_plan(FACTORS, "ccd", engine=engine)
    assert _inside(plan)
    assert not any(run.infeasible for run in plan.runs)


@pytest.mark.parametrize("engine", ["native", "auto", "pydoe"])
def test_rotatable_is_built_natively_and_flags_its_star_points(engine):
    """pyDOE cannot be asked for an arbitrary alpha and its adapter clips into the box, so the request is built where
    the star points can be kept - whichever engine was asked for."""
    plan = build_doe_plan(FACTORS, "ccd", engine=engine, ccd_alpha="rotatable")
    assert plan.notes.startswith("engine=native")
    flagged = [run for run in plan.runs if run.infeasible]
    assert len(flagged) == 2 * len(FACTORS)
    assert not _inside(plan)
    assert all(run.infeasible_reason and "星点超出因子范围" in run.infeasible_reason for run in flagged)


def test_alpha_one_is_the_face_centred_design_and_may_use_pydoe():
    default = build_doe_plan(FACTORS, "ccd", engine="auto")
    one = build_doe_plan(FACTORS, "ccd", engine="auto", ccd_alpha=1)
    assert [r.coded for r in default.runs] == [r.coded for r in one.runs]
    if pydoe_available():
        assert "engine=pydoe" in one.notes


# ── the API ──────────────────────────────────────────────────────────────────

client = TestClient(app)
BODY = {
    "product_type": "x",
    "application": "y",
    "domain": "anticorrosion_coating",
    "levers": [
        {"name": "树脂", "low": 10, "high": 40, "unit": "wt%"},
        {"name": "固化剂", "low": 5, "high": 20, "unit": "wt%"},
    ],
}


def _post(query: str):
    return client.post(f"/api/doe?{query}", json=BODY)


def test_the_api_default_ccd_stays_inside_every_range():
    r = _post("design=ccd&engine=native")
    assert r.status_code == 200, r.text
    plan = r.json()
    assert not any(run["infeasible"] for run in plan["runs"])
    lows = {f["name"]: f["low"] for f in plan["factors"]}
    highs = {f["name"]: f["high"] for f in plan["factors"]}
    for run in plan["runs"]:
        for name, value in run["natural"].items():
            assert lows[name] - 1e-6 <= value <= highs[name] + 1e-6


def test_the_api_rotatable_ccd_flags_the_star_runs():
    r = _post("design=ccd&engine=native&ccd_alpha=rotatable")
    assert r.status_code == 200, r.text
    plan = r.json()
    # the requirement can resolve more factors than the two levers it was given; the star points are 2 per factor
    flagged = {run["run_id"] for run in plan["runs"] if run["infeasible"]}
    outside = {run["run_id"] for run in plan["runs"] if any(abs(v) > 1 + 1e-9 for v in run["coded"].values())}
    assert flagged == outside
    assert len(flagged) == 2 * len(plan["factors"])
    assert "marked infeasible" in plan["notes"]


def test_the_api_refuses_a_bad_alpha_with_a_422_naming_it():
    for bad in ("nope", "-1", "0", "nan"):
        r = _post(f"design=ccd&engine=native&ccd_alpha={bad}")
        assert r.status_code == 422, (bad, r.status_code, r.text[:200])
        assert "ccd_alpha" in r.text


def test_the_api_ignores_ccd_alpha_for_other_designs():
    r = _post("design=full_factorial&engine=native&ccd_alpha=nope")
    assert r.status_code == 200, r.text

"""``ccd_alpha`` through the engines and the API: face-centred by default, rotatable on request, flagged not clipped -
and an inscribed variant that is rotatable *and* stays inside the factor ranges.

Round 4 left this open (plan #3): the native CCD put its star points at +/-(n_factorial)^0.25 - 1.68 coded units for
three factors, i.e. negative concentrations - and only said so in a note, while the pyDOE engine clipped the same points
onto the faces. The decision: face-centred (alpha = 1) is the default everywhere, a rotatable (or explicit-alpha) CCD is
available on request and keeps its star points outside the box, marked ``infeasible`` rather than clipped.
"""
from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.domain import doe as doe_domain
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


# ── inscribed: rotatable and inside ──────────────────────────────────────────


def _rotatability_gap(design: np.ndarray) -> float:
    """How far a design is from rotatable, by the classical moment condition: sum x_i^4 = 3 sum x_i^2 x_j^2 for every
    pair (the odd moments vanish by symmetry in these designs). 0 = rotatable."""
    k = design.shape[1]
    gap = 0.0
    for i in range(k):
        m4 = float(np.sum(design[:, i] ** 4))
        for j in range(k):
            if i != j:
                gap = max(gap, abs(m4 - 3.0 * float(np.sum(design[:, i] ** 2 * design[:, j] ** 2))))
    return gap


@pytest.mark.parametrize("k", [2, 3, 4, 5])
def test_the_inscribed_design_is_rotatable_and_never_leaves_the_box(k):
    inscribed = doe_domain.central_composite(k, "inscribed")
    assert _rotatability_gap(inscribed) < 1e-9
    assert float(np.max(np.abs(inscribed))) == pytest.approx(1.0)


@pytest.mark.parametrize("k", [2, 3, 4])
def test_only_the_rotatable_and_inscribed_designs_are_rotatable(k):
    """Not vacuous: the same check says face-centred and an arbitrary alpha are not."""
    assert _rotatability_gap(doe_domain.central_composite(k, "rotatable")) < 1e-9
    assert _rotatability_gap(doe_domain.central_composite(k, "face")) > 0.1
    assert _rotatability_gap(doe_domain.central_composite(k, 1.2)) > 0.1


@pytest.mark.parametrize("k", [2, 3, 4])
def test_the_inscribed_design_is_the_rotatable_one_shrunk_by_its_alpha(k):
    factorial_runs = 2**k
    alpha = factorial_runs**0.25
    rotatable = doe_domain.central_composite(k, "rotatable")
    inscribed = doe_domain.central_composite(k, "inscribed")
    assert np.allclose(inscribed, rotatable / alpha)
    # star points land exactly on the faces, factorial points on +-1/alpha
    star_rows = inscribed[factorial_runs : factorial_runs + 2 * k]
    assert np.allclose(np.sort(np.abs(star_rows[star_rows != 0])), 1.0)
    assert np.allclose(np.abs(inscribed[:factorial_runs]), 1.0 / alpha)


@pytest.mark.parametrize("alias", ["inscribed", "INSCRIBED", " Inscribed ", "cci"])
def test_the_inscribed_design_has_aliases_and_the_resolver_names_it(alias):
    assert np.allclose(doe_domain.central_composite(3, alias), doe_domain.central_composite(3, "inscribed"))
    with pytest.raises(ValueError, match="inscribed"):
        doe_domain.resolve_ccd_alpha("nope", 8)


@pytest.mark.parametrize("engine", ["native", "auto", "pydoe"])
def test_an_inscribed_request_stays_inside_every_range_on_every_engine(engine):
    plan = build_doe_plan(FACTORS, "ccd", engine=engine, ccd_alpha="inscribed")
    assert plan.notes.startswith("engine=native"), "pyDOE cannot be asked for it: built where it can be kept"
    assert _inside(plan)
    assert not any(run.infeasible for run in plan.runs)
    assert "Inscribed" in plan.notes and "every run inside" in plan.notes and "WARNING" not in plan.notes
    assert len(plan.runs) == len(doe_domain.central_composite(len(FACTORS), "inscribed"))


def test_the_inscribed_plan_trades_range_for_rotatability():
    """The price, stated: the factorial corners are pulled in to 1/alpha of the half-range."""
    face = build_doe_plan(FACTORS, "ccd", engine="native")
    inscribed = build_doe_plan(FACTORS, "ccd", engine="native", ccd_alpha="inscribed")
    low_a, high_a = FACTORS[0].low, FACTORS[0].high
    mid, half = (low_a + high_a) / 2, (high_a - low_a) / 2
    assert max(run.natural["A"] for run in face.runs) == pytest.approx(high_a)
    corners = [run.natural["A"] for run in inscribed.runs[:8]]
    assert max(corners) == pytest.approx(mid + half / 8**0.25, abs=1e-3)  # natural values are rounded to 4 places
    assert max(run.natural["A"] for run in inscribed.runs) == pytest.approx(high_a), "the star points still reach the faces"


def test_the_api_accepts_an_inscribed_ccd_and_its_422_lists_the_choices():
    r = _post("design=ccd&engine=native&ccd_alpha=inscribed")
    assert r.status_code == 200, r.text
    plan = r.json()
    assert not any(run["infeasible"] for run in plan["runs"])
    assert "Inscribed" in plan["notes"]
    bad = _post("design=ccd&engine=native&ccd_alpha=nope")
    assert bad.status_code == 422 and "inscribed" in bad.text

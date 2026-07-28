"""Inverse design: target properties → Pareto set of formulations.

The properties worth pinning are behavioural, not structural: that hard
constraints actually bind, that the answer contains *structurally different*
options rather than one recipe re-tuned, and that the search cannot wander
outside the surrogate's calibrated range and "win" by exploiting it.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain.schemas import (
    HardConstraint,
    ObjectiveSpec,
    ProductDomain,
    Requirement,
    TargetSpec,
)
from app.main import app
from app.services.inverse_design import design
from app.services.tradeoff_analysis import compute_pareto_ranks

client = TestClient(app)

# Small budget: these run in the suite, and the properties under test do not
# need a converged search.
_POP, _GENS = 20, 6


def _req() -> Requirement:
    return Requirement(
        domain=ProductDomain.anticorrosion_coating,
        salt_spray_hours=1000,
        voc_limit_gpl=250,
    )


def _targets() -> TargetSpec:
    return TargetSpec(
        hard=[
            HardConstraint(metric="voc_gpl", op="le", value=250),
            HardConstraint(metric="cost_cny_per_kg", op="le", value=40),
        ],
        soft=[
            ObjectiveSpec(metric="salt_spray_hours", direction="maximize", weight=0.5),
            ObjectiveSpec(metric="cost_cny_per_kg", direction="minimize", weight=0.5),
        ],
    )


@pytest.fixture(scope="module")
def result():
    return design(
        _req(), _targets(), population=_POP, generations=_GENS, seed_with_llm=False
    )


# ── constraints actually bind ────────────────────────────────────────────────


def test_feasible_candidates_satisfy_every_hard_constraint(result):
    targets = _targets()
    feasible = [c for c in result.candidates if c.feasible]
    assert feasible, "search produced no feasible candidate"
    for cand in feasible:
        for constraint in targets.hard:
            actual = cand.formulation.predicted.get(constraint.metric)
            assert constraint.violation(actual) == 0.0, (
                f"{cand.formulation.name}: {constraint.metric}={actual}"
            )


def test_infeasible_candidates_rank_below_every_feasible_one(result):
    """Constraint-domination: no amount of objective quality buys a violator a
    better rank than a candidate that actually meets the spec."""
    feasible = [c for c in result.candidates if c.feasible]
    infeasible = [c for c in result.candidates if not c.feasible]
    if not infeasible:
        pytest.skip("all candidates feasible in this run")
    assert max(c.pareto_rank for c in feasible) < min(
        c.pareto_rank for c in infeasible
    )


def test_missing_metric_counts_as_satisfied():
    """The predictor emits a domain-dependent, sparse key set; an absent metric
    must not reject a candidate on a constraint the model never evaluates."""
    assert HardConstraint(metric="nope", op="le", value=1).violation(None) == 0.0


# ── the actual deliverable: structurally different options ───────────────────


def test_returns_structurally_distinct_candidates(result):
    """Objective-space crowding alone collapses the population onto one
    composition; the answer to "show me options" must not be one recipe with
    the dial nudged."""
    signatures = {tuple(sorted(c.materials)) for c in result.candidates}
    assert len(signatures) >= 3, f"only {len(signatures)} distinct ingredient set(s)"


def test_search_changes_the_ingredient_set_not_just_ratios(result):
    """At least one candidate must differ from the baseline in *which*
    materials it uses — that is the capability the factor path lacks."""
    from app.pipeline import reconstruct

    baseline = set(reconstruct.genome_from_requirement(_req()).materials())
    assert any(set(c.materials) != baseline for c in result.candidates)


def test_no_duplicate_materials_within_a_candidate(result):
    """A swap can land on a material another slot already holds; listing it
    twice double-counts it in every role sum."""
    for cand in result.candidates:
        assert len(cand.materials) == len(set(cand.materials)), cand.materials


# ── the search must not exploit the surrogate ────────────────────────────────


def test_predictions_stay_physically_plausible(result):
    """The mechanistic predictor has no validity domain and extrapolates
    happily. A negative VOC would trivially satisfy a VOC ceiling, so an
    unbounded search "wins" by driving the model somewhere meaningless."""
    for cand in result.candidates:
        predicted = cand.formulation.predicted
        for metric in ("voc_gpl", "cost_cny_per_kg", "salt_spray_hours"):
            value = predicted.get(metric)
            if value is not None:
                assert value >= 0.0, f"{metric}={value} in {cand.formulation.name}"


def test_weights_close_to_100_and_non_negative(result):
    for cand in result.candidates:
        assert all(i.weight_pct >= 0 for i in cand.formulation.ingredients)
        assert cand.formulation.total_pct() == pytest.approx(100.0, abs=0.5)


# ── reporting ────────────────────────────────────────────────────────────────


def test_reports_search_effort_and_seeding(result):
    assert result.engine == "nsga2-numpy"
    assert result.generations == _GENS
    assert result.evaluations > _POP
    assert result.seeded_from["baseline"] == 1
    assert result.seeded_from["random"] > 0


def test_tradeoff_and_frontier_are_populated(result):
    assert result.tradeoff is not None
    assert result.pareto_frontier_ids
    assert 0 in {c.pareto_rank for c in result.candidates}


def test_runs_without_llm_seeding():
    out = design(_req(), _targets(), population=12, generations=2, seed_with_llm=False)
    assert out.candidates
    assert out.seeded_from["llm"] == 0


def test_no_hard_constraints_still_optimises():
    targets = TargetSpec(
        soft=[ObjectiveSpec(metric="salt_spray_hours", direction="maximize")]
    )
    out = design(_req(), targets, population=12, generations=2, seed_with_llm=False)
    assert out.candidates
    assert all(c.feasible for c in out.candidates)


def test_unsatisfiable_constraints_warn_rather_than_return_nothing():
    """An impossible spec should say so, not silently hand back an empty list."""
    targets = TargetSpec(
        hard=[HardConstraint(metric="salt_spray_hours", op="ge", value=10**9)],
        soft=[ObjectiveSpec(metric="salt_spray_hours", direction="maximize")],
    )
    out = design(_req(), targets, population=12, generations=2, seed_with_llm=False)
    assert out.candidates
    assert not any(c.feasible for c in out.candidates)
    assert out.warnings


# ── multi-rank Pareto ────────────────────────────────────────────────────────


def test_pareto_ranks_peel_successive_frontiers():
    objectives = [
        ObjectiveSpec(metric="a", direction="maximize"),
        ObjectiveSpec(metric="b", direction="maximize"),
    ]
    values = [[3.0, 3.0], [2.0, 2.0], [1.0, 1.0]]
    assert compute_pareto_ranks(values, objectives) == [0, 1, 2]


def test_pareto_ranks_handle_nan_without_looping():
    """NaN rows compare as neither dominating nor dominated, so a naive peel
    never empties the set."""
    objectives = [ObjectiveSpec(metric="a", direction="maximize")]
    ranks = compute_pareto_ranks([[float("nan")], [1.0], [2.0]], objectives)
    assert len(ranks) == 3


def test_pareto_ranks_empty():
    assert compute_pareto_ranks([], []) == []


# ── endpoint ─────────────────────────────────────────────────────────────────


def test_inverse_design_endpoint_accepts_and_completes():
    response = client.post(
        "/api/design/inverse",
        json={
            "requirement": _req().model_dump(mode="json"),
            "targets": _targets().model_dump(mode="json"),
            "population": 12,
            "generations": 2,
            "seed_with_llm": False,
        },
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["task_id"]
    assert body["stream_url"].endswith(f"{body['task_id']}/stream")

    status = client.get(body["status_url"])
    assert status.status_code == 200


def test_inverse_design_endpoint_validates_population():
    response = client.post(
        "/api/design/inverse",
        json={"requirement": _req().model_dump(mode="json"), "population": 1},
    )
    assert response.status_code == 422

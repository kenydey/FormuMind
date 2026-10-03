"""Substitution and inverse design rank their candidates on one shared ruler (round-3 P2-11).

``_score_and_validate`` scores one formulation at a time and normalises every metric that has no
user-given range (and no candidate-independent default) against ``(0, 2 × that candidate's own
value)`` — so a weak and a strong candidate both land on exactly 0.5 and their scores cannot be
compared. (VOC is such a metric whenever it exceeds 50 g/L; adhesion and hardness always are.) The
recommend pipeline already re-scores its batch on a shared range; substitution (``score_after``)
and inverse design (``formulation.score``) now do too.

The predictor is wrapped so two performance metrics follow one lever the tests control — with the
real predictor the swaps move cost and VOC only unless RDKit is installed, which would make the
properties depend on the environment.
"""
from __future__ import annotations

import pytest

from app.domain import knowledge
from app.domain.schemas import HardConstraint, ObjectiveSpec, ProductDomain, Requirement, TargetSpec
from app.pipeline import reconstruct
from app.services import predictor
from app.services.inverse_design import design
from app.services.substitution import find_substitutes

# Two performance metrics, neither with a candidate-independent default range.
OBJECTIVES = [
    ObjectiveSpec(metric="adhesion_mpa", direction="maximize", weight=0.5),
    ObjectiveSpec(metric="pencil_hardness_idx", direction="maximize", weight=0.5),
]


def _lever(form) -> float:
    """One number that grows with the inhibitor's catalog position and loading."""
    order = sorted(knowledge.RAW_MATERIALS)
    inhibitors = [i for i in form.ingredients if i.role == "inhibitor"]
    position = sum(order.index(i.name) for i in inhibitors if i.name in knowledge.RAW_MATERIALS)
    return position + 0.01 * sum(i.weight_pct for i in inhibitors)


def _controlled(metrics: dict, form) -> dict:
    x = _lever(form)
    return {**metrics, "adhesion_mpa": 2.0 + 0.05 * x, "pencil_hardness_idx": 3.0 + 0.08 * x}


@pytest.fixture(autouse=True)
def _controlled_performance(monkeypatch):
    monkeypatch.setattr("app.services.external_alternatives.external_substitutes_enabled", lambda: False)
    monkeypatch.setattr("app.services.surechembl_client.surechembl_enabled", lambda: False)

    real_full, real_predict = predictor.predict_full, predictor.predict

    def predict_full(form, *args, **kwargs):
        predicted, std = real_full(form, *args, **kwargs)
        return _controlled(predicted, form), std

    def predict(form, *args, **kwargs):
        return _controlled(real_predict(form, *args, **kwargs), form)

    monkeypatch.setattr(predictor, "predict_full", predict_full)
    monkeypatch.setattr(predictor, "predict", predict)


def _slot_of(genome, role: str) -> int:
    return next(i for i, s in enumerate(genome.slots) if s.role == role)


def _substitutes(objectives):
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        voc_limit_gpl=420,
        salt_spray_hours=500,
        objectives=objectives,
    )
    genome = reconstruct.genome_from_requirement(req)
    return find_substitutes(
        genome,
        _slot_of(genome, "inhibitor"),
        req,
        include_external=False,
        include_literature=False,
        include_surechembl=False,
        include_llm=False,
    )["candidates"]


def test_substitution_scores_are_comparable_across_candidates():
    catalog = [c for c in _substitutes(OBJECTIVES) if c["source"] == "catalog"]
    assert len(catalog) >= 2

    adhesion = [c["deltas"]["adhesion_mpa"]["after"] for c in catalog]
    assert len(set(round(a, 6) for a in adhesion)) == len(catalog), "the swaps must differ for the test to mean anything"

    scores = [c["score_after"] for c in catalog]
    # Before: every swap normalised against its own value and came out the same number.
    assert len({round(s, 9) for s in scores}) == len(catalog)
    # And the better swap (higher on both objectives) scores higher.
    ordered = [score for _a, score in sorted(zip(adhesion, scores))]
    assert ordered == sorted(ordered)


def test_a_single_maximize_objective_keeps_the_raw_predicted_value():
    """The raw score needs no ruler; rescoring must leave it alone."""
    one = [ObjectiveSpec(metric="adhesion_mpa", direction="maximize", weight=1.0)]
    for cand in _substitutes(one):
        if cand["source"] == "catalog":
            assert cand["score_after"] == pytest.approx(cand["deltas"]["adhesion_mpa"]["after"], abs=1e-3)  # deltas are rounded


# ── inverse design ───────────────────────────────────────────────────────────


@pytest.fixture
def designed():
    req = Requirement(domain=ProductDomain.anticorrosion_coating, salt_spray_hours=1000, voc_limit_gpl=420)
    targets = TargetSpec(
        hard=[HardConstraint(metric="voc_gpl", op="le", value=420)],
        soft=OBJECTIVES,
    )
    return design(req, targets, population=16, generations=4, seed_with_llm=False)


def test_inverse_design_scores_the_final_population_on_one_ruler(designed):
    forms = [c.formulation for c in designed.candidates]
    assert len(forms) >= 4
    levers = [round(_lever(f), 6) for f in forms]
    assert len(set(levers)) > 1, "the population must vary for the test to mean anything"

    # Before: each design normalised against its own value — 0.5 for everyone.
    assert len({round(f.score, 9) for f in forms}) > 1
    # A design that is better on both objectives scores higher; equal designs tie.
    for a in forms:
        for b in forms:
            if _lever(a) > _lever(b) + 1e-9:
                assert a.score > b.score

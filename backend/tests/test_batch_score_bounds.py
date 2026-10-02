"""P1-3: ranking a recommend batch must use one shared normalisation range.

``_score_and_validate`` scores each candidate alone and (for a metric with no
user-given range) normalises it against ``(0, 2 x its own predicted value)`` —
so a weak and a strong candidate both land on exactly 0.5 and the order that
``finalize_scored_formulations`` sorts by is just the LLM's output order. The
batch is now re-scored against :func:`predictor.shared_bounds`; the objectives
the pipeline resolved are also the ones candidates are scored by.
"""
from __future__ import annotations

import math

import pytest

from app.domain.schemas import Formulation, Ingredient, ObjectiveSpec, ProductDomain, Requirement
from app.services import predictor
from app.services import recommend_pipeline as rp


def _obj(metric: str, direction: str = "maximize", **kw) -> ObjectiveSpec:
    return ObjectiveSpec(metric=metric, direction=direction, weight=1.0, **kw)


def _form(name: str, predicted: dict[str, float]) -> Formulation:
    f = Formulation(
        name=name,
        domain=ProductDomain.anticorrosion_coating,
        # Distinct compositions: the pipeline de-duplicates look-alike candidates.
        ingredients=[Ingredient(name=f"树脂-{name}", role="resin", weight_pct=100.0)],
    )
    f.predicted = dict(predicted)
    return f


@pytest.fixture()
def fake_predict(monkeypatch):
    """predictor.predict(form, process) -> form.predicted (no chemistry stack)."""
    monkeypatch.setattr(predictor, "predict", lambda form, process=None: dict(form.predicted))


OBJS = [_obj("adhesion"), _obj("hardness")]
WEAK = {"adhesion": 2.0, "hardness": 60.0}
STRONG = {"adhesion": 5.0, "hardness": 120.0}


def test_per_candidate_bounds_cannot_tell_weak_from_strong(fake_predict):
    """The defect: identical 0.5 for both — documents why shared bounds exist."""
    scores = []
    for vals in (WEAK, STRONG):
        f = _form("c", vals)
        b = predictor.default_bounds(OBJS, f)
        scores.append(predictor.multi_objective_score(f, OBJS, None, b))
    assert scores[0] == pytest.approx(scores[1])


def test_shared_bounds_rank_strong_above_weak(fake_predict):
    forms = [_form("w", WEAK), _form("s", STRONG)]
    props = [dict(f.predicted) for f in forms]
    shared = predictor.shared_bounds(OBJS, props)
    w, s = (predictor.multi_objective_score(f, OBJS, None, shared, props=p) for f, p in zip(forms, props))
    assert s > w
    assert 0.0 <= w <= s <= 1.0


def test_shared_bounds_respect_user_ranges_and_widen_the_rest(fake_predict):
    objs = [_obj("adhesion", ref_min=1.0, ref_max=10.0), _obj("hardness")]
    shared = predictor.shared_bounds(objs, [WEAK, STRONG])
    assert shared["adhesion"] == (1.0, 10.0)  # explicit range is authoritative
    assert shared["hardness"] == (0.0, 120.0)  # widened to the best candidate


def test_shared_bounds_include_match_target_so_overshoot_is_penalised(fake_predict):
    objs = [_obj("adhesion", "match_target", target_value=8.0), _obj("hardness")]
    forms = [_form("exact", {"adhesion": 8.0, "hardness": 90.0}),
             _form("over", {"adhesion": 12.0, "hardness": 90.0}),
             _form("under", {"adhesion": 3.0, "hardness": 90.0})]
    props = [dict(f.predicted) for f in forms]
    shared = predictor.shared_bounds(objs, props)
    assert shared["adhesion"] == (0.0, 12.0)
    sc = [predictor.multi_objective_score(f, objs, None, shared, props=p) for f, p in zip(forms, props)]
    assert sc[0] > sc[1] and sc[0] > sc[2]


def test_shared_bounds_ignore_non_finite_values(fake_predict):
    shared = predictor.shared_bounds(OBJS, [{"adhesion": float("nan"), "hardness": 50.0}, STRONG])
    assert all(math.isfinite(v) for lo_hi in shared.values() for v in lo_hi)
    assert shared["adhesion"] == (0.0, 5.0)


def test_multi_objective_score_reuses_given_props(monkeypatch):
    calls = []
    monkeypatch.setattr(predictor, "predict", lambda f, p=None: calls.append(1) or dict(f.predicted))
    f = _form("c", WEAK)
    predictor.multi_objective_score(f, OBJS, None, {"adhesion": (0, 5), "hardness": (0, 120)}, props=WEAK)
    assert calls == []


# ── the batch re-score ──────────────────────────────────────────────────────


def _collapsed(forms: list[Formulation], process=None) -> None:
    """Score as _score_and_validate does: each candidate on its own bounds."""
    for f in forms:
        f.score = predictor.multi_objective_score(f, OBJS, process, predictor.default_bounds(OBJS, f))


def test_rescore_orders_the_batch_by_quality(fake_predict):
    weak, strong = _form("weak", WEAK), _form("strong", STRONG)
    _collapsed([weak, strong])
    assert weak.score == pytest.approx(strong.score)  # the bug, before

    rp._rescore_with_shared_bounds([weak, strong], OBJS, None)

    assert strong.score > weak.score
    ordered = sorted([weak, strong], key=lambda f: f.score, reverse=True)
    assert [f.name for f in ordered] == ["strong", "weak"]


def test_rescore_keeps_the_kg_multiplier(fake_predict):
    plain, penalised = _form("plain", STRONG), _form("penalised", STRONG)
    _collapsed([plain, penalised])
    penalised.score *= 0.5  # what kg_compat_adjust does to an INHIBITS hit

    rp._rescore_with_shared_bounds([plain, penalised], OBJS, None)

    assert penalised.score == pytest.approx(plain.score * 0.5)
    assert plain.score > penalised.score


def test_rescore_skips_single_objective_and_empty_batches(fake_predict):
    f = _form("only", STRONG)
    f.score = 123.0
    rp._rescore_with_shared_bounds([f], [_obj("adhesion")], None)
    assert f.score == 123.0  # single objective: raw predicted value is the score
    rp._rescore_with_shared_bounds([], OBJS, None)  # no error


def test_rescore_is_fail_open(monkeypatch):
    def boom(form, process=None):
        raise RuntimeError("predictor down")

    monkeypatch.setattr(predictor, "predict", boom)
    f = _form("c", WEAK)
    f.score = 0.42
    rp._rescore_with_shared_bounds([f], OBJS, None)
    assert f.score == 0.42


# ── wiring into finalize_recommendation_bundle ──────────────────────────────


def test_finalize_bundle_scores_by_resolved_objectives_and_ranks_by_quality(monkeypatch, fake_predict):
    from app.domain.schemas import RecommendedFormula
    import app.domain.formulation_gate as gate
    import app.pipeline.claim_checker as claim
    import app.pipeline.workflow as wf

    forms = {"weak": _form("weak", WEAK), "strong": _form("strong", STRONG)}
    recs = [RecommendedFormula(name=n, domain=ProductDomain.anticorrosion_coating) for n in forms]
    seen_objectives: list = []

    def fake_score_and_validate(form, process=None, req=None, **kw):
        seen_objectives.append(kw.get("objectives"))
        _collapsed([form])
        return form

    monkeypatch.setattr(gate, "recommended_to_formulation", lambda rec: forms[rec.name])
    monkeypatch.setattr(gate, "validate_formulations", lambda scored, req=None: (scored, []))
    monkeypatch.setattr(claim, "check_formulation_predictions", lambda form, evidence: [])
    monkeypatch.setattr(wf, "_score_and_validate", fake_score_and_validate)

    # No objectives on the request itself: the resolved list must still be used.
    req = Requirement.model_validate(
        {"product_name": "t", "domain": "anticorrosion_coating", "objectives": []}
    )

    class _S:
        recommend_diversity_enabled = False
        recommend_default_n = 5
        recommend_max_n = 5
        recommend_diversity_lambda = 0.5
        recommend_tradeoff_enabled = False
        kg_enabled = False

    aligned, scored, _warnings, _n, _div, _trade = rp.finalize_recommendation_bundle(
        recs, req, [], requested_n=2, objectives=OBJS, include_tradeoff=False, settings=_S(),
    )

    assert all(o is OBJS for o in seen_objectives), seen_objectives
    assert [f.name for f in scored] == ["strong", "weak"]
    assert [r.name for r in aligned] == ["strong", "weak"]

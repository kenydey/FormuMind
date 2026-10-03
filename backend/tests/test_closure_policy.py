"""One policy for "do the weights add up to 100 %" — and a score that respects it.

The question was asked in five places with four answers (±0.5, ±5, ±5, ±8, and the feasibility
gate never asked): the same 66 % recipe produced three differently-worded warnings in one
response, passed the feasibility gate and ranked exactly like a complete recipe. Related:
ranking on a *single* ``minimize`` / ``match_target`` objective used the raw predicted value,
so the recipe with the highest VOC came first.
"""
from __future__ import annotations

import pytest

from app.domain import closure
from app.domain.closure import assess, discount_score, is_closure_warning, warning_text
from app.domain.formulation_gate import validate_formulations, validate_recommended_formulas
from app.domain.schemas import ObjectiveSpec, ProductDomain, Requirement


# ── the policy itself ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "total,level",
    [(100.0, "ok"), (99.5, "ok"), (100.5, "ok"), (99.4, "warn"), (105.0, "warn"), (95.0, "warn"),
     (94.9, "error"), (105.1, "error"), (66.0, "error"), (130.0, "error")],
)
def test_levels_and_their_boundaries(total, level):
    assert assess(total).level == level


def test_a_closed_recipe_has_nothing_to_say_and_loses_no_score():
    assert warning_text(100.2) is None
    assert assess(100.2).score_factor == 1.0
    assert discount_score(872.9, 100.2) == 872.9


def test_the_warning_names_the_recipe_the_gap_and_the_discount():
    text = warning_text(66.0, "recipe-A")
    assert text.startswith("recipe-A: ") and is_closure_warning(text)
    assert "66.0%" in text and "expected ~100%" in text
    assert "incomplete" in text and "discounted 20%" in text
    assert "over-full" in warning_text(130.0)
    # a small gap is a warning with a small discount, not "incomplete"
    mild = warning_text(97.0)
    assert "incomplete" not in mild and "over-full" not in mild and "discounted 2.5%" in mild
    # the message the frontend classifies as a weight warning keeps its keywords
    assert "weight" in text.lower() and "~100%" in text


def test_the_discount_is_capped_and_never_improves_a_score():
    assert assess(40.0).score_factor == pytest.approx(0.8)
    assert assess(97.0).score_factor == pytest.approx(0.975)
    # multiplying a negative score by 0.8 would *raise* it; the discount comes off the magnitude
    assert discount_score(100.0, 66.0) == pytest.approx(80.0)
    assert discount_score(-100.0, 66.0) == pytest.approx(-120.0)
    assert discount_score(0.0, 66.0) == 0.0


def test_total_of_ignores_missing_weights():
    assert closure.total_of([40.0, None, 60.0]) == 100.0


# ── every check agrees ──────────────────────────────────────────────────────


def _scaled(factor: float, name: str = "recipe"):
    from app.domain import knowledge

    req = Requirement(domain=ProductDomain.anticorrosion_coating)
    base = knowledge.offline_recommend_fallback(req, n=1)[0]
    ings = [i.model_copy(update={"weight_pct": i.weight_pct * factor}) for i in base.ingredients]
    return base.model_copy(update={"ingredients": ings, "name": name}), req


@pytest.mark.parametrize("factor,expect", [(1.0, 0), (0.97, 1), (0.66, 1)])
def test_a_recipe_gets_exactly_one_closure_warning_per_channel(factor, expect):
    from app.pipeline.workflow import _score_and_validate, process_for

    form, req = _scaled(factor)
    _, bundle = validate_formulations([form], req)
    assert len([w for w in bundle if is_closure_warning(w)]) == expect  # was 3 for a 66 % recipe
    scored = _score_and_validate(form, process_for(req), req, enrich_network=False)
    assert len([w for w in scored.warnings if is_closure_warning(w)]) == expect
    if expect:
        assert all(w.startswith("recipe: ") for w in bundle if is_closure_warning(w))


def test_the_recommended_components_check_uses_the_same_tolerance():
    """It used ±8, so a recipe 6 % short was silent here and loud everywhere else."""
    from app.domain.formulation_gate import formulation_to_recommended

    form, _ = _scaled(0.94, "short")  # 6 % short
    rec = formulation_to_recommended(form)
    _, warnings = validate_recommended_formulas([rec])
    closing = [w for w in warnings if is_closure_warning(w)]
    assert len(closing) == 1 and "short" in closing[0] and "incomplete" in closing[0]

    ok_form, _ = _scaled(1.0, "full")
    _, quiet = validate_recommended_formulas([formulation_to_recommended(ok_form)])
    assert not [w for w in quiet if is_closure_warning(w)]


def test_the_feasibility_gate_rejects_an_incomplete_recipe_and_flags_a_slightly_off_one():
    from app.services.feasibility import check_formulation

    full, req = _scaled(1.0)
    assert check_formulation(full, req).feasible

    incomplete, _ = _scaled(0.66)
    verdict = check_formulation(incomplete, req)
    assert verdict.feasible is False and verdict.status == "intercept"
    assert any(r.startswith("[CLOSURE]") and "incomplete" in r for r in verdict.reasons)

    slightly_off, _ = _scaled(0.97)
    verdict = check_formulation(slightly_off, req)
    assert verdict.feasible is True
    assert verdict.status in ("warn", "intercept") and verdict.status != "intercept"
    assert any(r.startswith("[CLOSURE]") for r in verdict.reasons)


# ── ranking ─────────────────────────────────────────────────────────────────


def test_an_incomplete_recipe_scores_below_the_same_recipe_completed():
    from app.pipeline.workflow import _score_and_validate, process_for

    full, req = _scaled(1.0)
    partial, _ = _scaled(0.66)
    process = process_for(req)
    a = _score_and_validate(full, process, req, enrich_network=False)
    b = _score_and_validate(partial, process, req, enrich_network=False)
    # raw single-maximize score = predicted value; the partial one is discounted by the cap
    from app.pipeline.workflow import default_objectives

    metric = (req.objectives or default_objectives(req.domain))[0].metric
    assert a.score == pytest.approx(a.predicted[metric])
    assert b.score == pytest.approx(b.predicted[metric] * 0.8)


def _rank(req, forms):
    from app.pipeline.workflow import _score_and_validate, process_for
    from app.services.recommend_pipeline import _rescore_with_shared_bounds

    process = process_for(req)
    scored = [_score_and_validate(f, process, req, enrich_network=False) for f in forms]
    _rescore_with_shared_bounds(scored, req.objectives, process)
    return scored


def _candidates(req):
    from app.domain import knowledge

    return knowledge.offline_recommend_fallback(req, n=3)


def test_a_single_minimize_objective_ranks_the_lowest_value_first():
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        objectives=[ObjectiveSpec(metric="voc_gpl", direction="minimize", weight=1.0)],
    )
    scored = _rank(req, _candidates(req))
    by_score = sorted(scored, key=lambda f: f.score, reverse=True)
    vocs = [f.predicted["voc_gpl"] for f in by_score]
    assert len(set(vocs)) == len(vocs), "fixture must give distinct VOC values"
    assert vocs == sorted(vocs), f"highest score must be the lowest VOC, got {vocs}"
    assert all(0.0 <= f.score <= 1.0 for f in scored)


def test_a_single_match_target_objective_ranks_the_closest_first():
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        objectives=[ObjectiveSpec(metric="voc_gpl", direction="match_target", target_value=240.0, weight=1.0)],
    )
    scored = _rank(req, _candidates(req))
    by_score = sorted(scored, key=lambda f: f.score, reverse=True)
    gaps = [abs(f.predicted["voc_gpl"] - 240.0) for f in by_score]
    assert gaps == sorted(gaps), f"closest to target must rank first, got gaps {gaps}"


def test_a_single_maximize_objective_keeps_its_raw_score():
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        objectives=[ObjectiveSpec(metric="salt_spray_hours", direction="maximize", weight=1.0)],
    )
    for f in _rank(req, _candidates(req)):
        assert f.score == pytest.approx(f.predicted["salt_spray_hours"])


def test_score_is_raw_truth_table():
    from app.services.predictor import score_is_raw

    mx = ObjectiveSpec(metric="a", direction="maximize")
    mn = ObjectiveSpec(metric="a", direction="minimize")
    assert score_is_raw([mx]) is True
    assert score_is_raw([mn]) is False
    assert score_is_raw([mx, mx]) is False
    assert score_is_raw([]) is False
    assert score_is_raw(None) is False

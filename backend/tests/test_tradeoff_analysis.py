"""P1-R2 trade-off analysis unit tests."""
from __future__ import annotations

from app.domain.schemas import Ingredient, ObjectiveSpec, ProductDomain, Formulation
from app.services.tradeoff_analysis import analyze_tradeoffs, compute_pareto_mask, _build_scenario_picks
from app.domain.tradeoff_schemas import FormulationCandidateView, GroundingSummary


def _form(name: str, salt: float, cost: float, score: float = 0.5) -> Formulation:
    return Formulation(
        name=name,
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[Ingredient(name="Epoxy", role="resin", weight_pct=50.0)],
        predicted={"salt_spray_hours": salt, "cost_cny_per_kg": cost, "voc_gpl": 100.0},
        score=score,
    )


def test_pareto_dominance():
    """T-02: dominated candidate excluded from frontier."""
    objectives = [
        ObjectiveSpec(metric="salt_spray_hours", direction="maximize"),
        ObjectiveSpec(metric="cost_cny_per_kg", direction="minimize"),
    ]
    values = [[800.0, 20.0], [600.0, 25.0]]
    mask = compute_pareto_mask(values, objectives)
    assert mask[0] is True
    assert mask[1] is False


def test_pareto_dominance_match_target():
    """A candidate exactly on a match_target objective must not be treated as
    dominated by one far off-target just because its raw value is lower."""
    objectives = [
        ObjectiveSpec(metric="ph", direction="match_target", target_value=7.0),
        ObjectiveSpec(metric="cost_cny_per_kg", direction="minimize"),
    ]
    on_target = [7.0, 50.0]
    off_target = [12.0, 50.0]
    mask = compute_pareto_mask([on_target, off_target], objectives)
    assert mask[0] is True
    assert mask[1] is False


def _candidate(id_: str, value: float, pareto: bool = True) -> FormulationCandidateView:
    return FormulationCandidateView(
        id=id_,
        name=id_,
        score=0.5,
        predicted={"cost_cny_per_kg": value},
        pareto=pareto,
        pareto_rank=0,
        confidence="medium",
        grounding=GroundingSummary(),
    )


def test_best_performance_pick_respects_minimize_direction():
    """objectives[0] minimizing cost must pick the cheapest candidate, not the
    most expensive one (max() over raw values picks the worst for minimize)."""
    candidates = [_candidate("cheap", 10.0), _candidate("pricey", 90.0)]
    objectives = [ObjectiveSpec(metric="cost_cny_per_kg", direction="minimize")]
    picks = _build_scenario_picks(candidates, objectives, ["best_performance"])
    pick = next(p for p in picks if p.scenario == "best_performance")
    assert pick.candidate_id == "cheap"


def test_analyze_tradeoffs_nonempty_frontier():
    """T-01: tradeoff returns pareto ids."""
    forms = [
        _form("High salt", 800, 22, 0.9),
        _form("Low cost", 650, 14, 0.75),
        _form("Dominated", 600, 25, 0.5),
    ]
    objectives = [
        ObjectiveSpec(metric="salt_spray_hours", direction="maximize"),
        ObjectiveSpec(metric="cost_cny_per_kg", direction="minimize"),
    ]
    result = analyze_tradeoffs(forms, objectives)
    assert result is not None
    assert result.pareto_frontier_ids
    assert len(result.comparison_table) == 3
    assert any(p.scenario == "best_performance" for p in result.scenario_picks)

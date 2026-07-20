"""P1-R2 trade-off analysis unit tests."""
from __future__ import annotations

from app.domain.schemas import Ingredient, ObjectiveSpec, ProductDomain, Formulation
from app.services.tradeoff_analysis import analyze_tradeoffs, compute_pareto_mask


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

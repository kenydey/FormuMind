"""Post-A′ #2: Requirement effect tracing + objective change → explain hit."""
from __future__ import annotations

from app.domain.schemas import (
    Formulation,
    Ingredient,
    LeverSpec,
    ObjectiveSpec,
    ProductDomain,
    Requirement,
)
from app.services.formulation_explain import build_formulation_explain
from app.services.requirement_effect_trace import (
    build_requirement_effect_trace,
    effect_trace_summary,
)


def test_effect_trace_marks_objectives_and_voc_wired():
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        objectives=[
            ObjectiveSpec(metric="salt_spray_hours", direction="maximize", target_value=800.0)
        ],
        levers=[LeverSpec(name="Zinc phosphate", low=2.0, high=14.0)],
        voc_limit_gpl=250.0,
        notes="display only note",
        ph_target=7.0,
    )
    trace = build_requirement_effect_trace(req)
    fields = {t["field"]: t for t in trace}
    assert fields["objectives.salt_spray_hours"]["status"] == "wired"
    assert "recommend" in fields["objectives.salt_spray_hours"]["consumers"]
    assert fields["levers.Zinc phosphate"]["status"] == "wired"
    assert fields["voc_limit_gpl"]["status"] == "wired"
    assert fields["notes"]["status"] == "display_only"
    assert fields["ph_target"]["status"] == "display_only"
    summary = effect_trace_summary(trace)
    assert summary["wired"] >= 3
    assert summary["display_only"] >= 1


def test_changing_objective_changes_explain_hit():
    form = Formulation(
        name="F",
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[Ingredient(name="Epoxy", role="resin", weight_pct=40.0)],
        predicted={"salt_spray_hours": 900.0, "voc_gpl": 120.0},
    )
    req_a = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        objectives=[
            ObjectiveSpec(metric="salt_spray_hours", direction="maximize", target_value=800.0)
        ],
    )
    req_b = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        objectives=[ObjectiveSpec(metric="adhesion", direction="maximize", target_value=5.0)],
    )
    ea = build_formulation_explain(form, requirement=req_a)
    eb = build_formulation_explain(form, requirement=req_b)
    assert any("salt_spray_hours" in h for h in ea.objectives_hit)
    assert any("adhesion" in m for m in eb.constraints_miss)
    assert ea.effect_trace
    assert any(t["field"] == "objectives.salt_spray_hours" for t in ea.effect_trace)
    assert any(t["field"] == "objectives.adhesion" for t in eb.effect_trace)

"""Batch C: Formulation.explain structured contract."""
from __future__ import annotations

from app.domain.schemas import (
    Formulation,
    FormulationExplain,
    Ingredient,
    ObjectiveSpec,
    ProductDomain,
)
from app.services.formulation_explain import attach_explain, build_formulation_explain


def _form(**kwargs) -> Formulation:
    base = dict(
        name="F1",
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[
            Ingredient(
                name="Zinc phosphate",
                role="pigment",
                weight_pct=8.0,
                evidence_refs=["patent:US123", "lit:doi:10.1/x"],
            ),
            Ingredient(name="Epoxy", role="resin", weight_pct=40.0),
        ],
        rationale="barrier + active inhibition",
        predicted={"salt_spray_hours": 900.0, "voc_gpl": 120.0},
        predicted_std={"salt_spray_hours": 250.0},
        prediction_tiers={"voc_gpl": "empirical"},
        score=0.8,
        warnings=["供应风险：Zinc phosphate lead_time 长"],
        kg_compat={
            "feasible": True,
            "status": "pass",
            "incompatible_pairs": [],
            "synergy_pairs": [{"a": "Epoxy", "b": "Amine", "relation": "synergizes"}],
            "measured_materials": ["Zinc phosphate"],
            "measured_metric_hits": [
                {
                    "material": "Zinc phosphate",
                    "metric": "salt_spray_hours",
                    "quality": "good",
                    "value": 950.0,
                }
            ],
        },
        bias_corrected_metrics=[],
    )
    base.update(kwargs)
    return Formulation(**base)


def test_build_explain_hits_and_uncertainty():
    form = _form()
    objs = [
        ObjectiveSpec(metric="salt_spray_hours", direction="maximize", target_value=800.0),
        ObjectiveSpec(metric="voc_gpl", direction="minimize", ref_max=150.0),
    ]
    explain = build_formulation_explain(form, objectives=objs)
    assert isinstance(explain, FormulationExplain)
    assert form.explain is explain
    assert any("salt_spray_hours" in h for h in explain.objectives_hit)
    assert any("KG实测" in h for h in explain.objectives_hit)
    assert explain.evidence_refs
    assert explain.evidence_refs[0]["source_type"] == "patent"
    assert "Epoxy↔Amine" in explain.kg_signals.get("synergizes", [])
    assert explain.supply_flags
    assert any("不确定性" in u or "empirical" in u for u in explain.uncertainty)
    assert explain.bias_corrected is False


def test_bias_corrected_flag():
    form = _form(bias_corrected_metrics=["salt_spray_hours"])
    explain = attach_explain(form).explain
    assert explain is not None
    assert explain.bias_corrected is True
    assert "salt_spray_hours" in explain.bias_corrected_metrics


def test_constraints_miss_when_no_prediction():
    form = _form(predicted={"voc_gpl": 50.0})
    objs = [ObjectiveSpec(metric="adhesion", direction="maximize")]
    explain = build_formulation_explain(form, objectives=objs)
    assert any("adhesion" in m for m in explain.constraints_miss)

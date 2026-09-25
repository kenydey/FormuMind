"""Explain smoke: scoring path attaches Formulation.explain (Batch C polish)."""
from __future__ import annotations

from app.domain.schemas import (
    Formulation,
    Ingredient,
    ObjectiveSpec,
    ProductDomain,
    Requirement,
)
from app.pipeline.workflow import _score_and_validate


def test_score_and_validate_attaches_explain():
    form = Formulation(
        name="Smoke",
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[
            Ingredient(name="Epoxy", role="resin", weight_pct=40.0),
            Ingredient(
                name="Zinc phosphate",
                role="pigment",
                weight_pct=8.0,
                evidence_refs=["patent:US1"],
            ),
        ],
        predicted={"salt_spray_hours": 800.0, "voc_gpl": 100.0},
        predicted_std={"salt_spray_hours": 50.0},
        rationale="smoke",
    )
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        objectives=[
            ObjectiveSpec(metric="salt_spray_hours", direction="maximize", target_value=720.0),
        ],
    )
    # Signature: (form, process, req, *, chem_screen=...)
    out = _score_and_validate(form, None, req, chem_screen=False, enrich_network=False)
    assert out.explain is not None
    assert out.score is not None

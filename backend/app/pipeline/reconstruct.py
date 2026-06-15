"""Rebuild a Formulation from DOE/experiment factor values.

Kept dependency-light (only the domain knowledge base) so it can be imported by
both the optimization workflow and the training service without import cycles.
"""
from __future__ import annotations

from ..domain import knowledge
from ..domain.schemas import Formulation, ProductDomain, Requirement


def formulation_from_factors(domain: ProductDomain, factors: dict[str, float]) -> Formulation:
    """Apply natural-unit lever values onto the domain's baseline formulation.

    Keys matching an ingredient name override that ingredient's weight percent;
    non-ingredient keys (e.g. ``cure_temperature_c``) are ignored here and are
    handled as process features elsewhere. Weights are re-balanced to ~100%.
    """
    base = knowledge.baseline_formulation(Requirement(domain=domain))
    overrides = dict(factors)
    ings = []
    for ing in base.ingredients:
        new = ing.model_copy(deep=True)
        if new.name in overrides:
            new.weight_pct = round(float(overrides[new.name]), 4)
        ings.append(new)
    return knowledge._balanced(base.name, base.domain, ings, base.rationale)

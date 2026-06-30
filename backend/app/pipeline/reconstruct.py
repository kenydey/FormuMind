"""Rebuild a Formulation from DOE/experiment factor values.

Kept dependency-light (only the domain knowledge base) so it can be imported by
both the optimization workflow and the training service without import cycles.
"""
from __future__ import annotations

from ..domain import knowledge
from ..domain.levers import is_process_lever
from ..domain.project_spec import normalize_requirement, resolve_levers
from ..domain.schemas import Formulation, ProductDomain, Requirement

# Dilute aqueous baths: g/L ≈ wt% × 10 (density ~1 g/mL).
_G_PER_L_TO_WT_PCT = 0.1


def formulation_from_factors(
    req: Requirement | ProductDomain,
    factors: dict[str, float],
) -> Formulation:
    """Apply natural-unit lever values onto the substrate-aware baseline formulation.

    Keys matching an ingredient name override that ingredient's weight percent;
    process keys (e.g. ``cure_temperature_c``, ``immersion_time_min``) are ignored
    here and handled as process features elsewhere. Weights are re-balanced to ~100%.
    """
    requirement = req if isinstance(req, Requirement) else Requirement(domain=req)
    requirement = normalize_requirement(requirement)
    base = knowledge.baseline_formulation(requirement)
    levers = resolve_levers(requirement, base)
    unit_map = {lev.name: lev.unit for lev in levers}
    overrides = dict(factors)
    ings = []
    for ing in base.ingredients:
        new = ing.model_copy(deep=True)
        if new.name in overrides and not is_process_lever(new.name):
            raw = float(overrides[new.name])
            unit = unit_map.get(new.name, "wt%")
            if unit == "g/L":
                raw *= _G_PER_L_TO_WT_PCT
            new.weight_pct = round(raw, 4)
        ings.append(new)
    return knowledge._balanced(base.name, base.domain, ings, base.rationale)

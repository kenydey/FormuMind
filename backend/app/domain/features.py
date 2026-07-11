"""Formulation featurization — the shared feature language used by both the
empirical predictor and the data-driven (trained) models.

A formulation is reduced to a fixed-order numeric vector of role-based
composition, derived chemistry descriptors, and process parameters, so feature
vectors align across every experiment record regardless of which specific raw
materials were used.
"""
from __future__ import annotations

from .chemistry import resin_hardener_weight_ratio
from .schemas import Formulation

ROLE_KEYS = [
    "resin", "hardener", "inhibitor", "pigment", "filler", "surfactant",
    "builder", "solvent", "active", "accelerator", "chelant", "additive",
]
DERIVED_KEYS = ["resin_hardener_ratio", "waterborne", "total_solids"]
# Process parameters that can act as DOE/optimization levers. Must cover every
# name in levers._PROCESS_LEVER_NAMES so process factors reach trained models.
PROCESS_KEYS = ["cure_temperature_c", "bath_temperature_c", "immersion_time_min"]

FEATURE_KEYS: list[str] = ROLE_KEYS + DERIVED_KEYS + PROCESS_KEYS


def featurize(form: Formulation, process: dict | None = None) -> dict[str, float]:
    """Return an ordered feature dict for a formulation."""
    process = process or {}
    roles = {k: 0.0 for k in ROLE_KEYS}
    for ing in form.ingredients:
        key = ing.role if ing.role in roles else "additive"
        roles[key] += ing.weight_pct

    ratio = resin_hardener_weight_ratio(form) or 0.0
    waterborne = 1.0 if any(
        i.name == "Deionized water" and i.weight_pct > 30 for i in form.ingredients
    ) else 0.0
    total_solids = max(0.0, 100.0 - roles["solvent"])

    feats: dict[str, float] = dict(roles)
    feats["resin_hardener_ratio"] = float(ratio)
    feats["waterborne"] = waterborne
    feats["total_solids"] = round(total_solids, 4)
    for key in PROCESS_KEYS:
        feats[key] = float(process.get(key, 0.0))
    return feats


def vector(form: Formulation, process: dict | None = None) -> list[float]:
    feats = featurize(form, process)
    return [feats[k] for k in FEATURE_KEYS]

"""Formulation featurization — the shared feature language used by both the
empirical predictor and the data-driven (trained) models.

A formulation is reduced to a fixed-order numeric vector of role-based
composition, derived chemistry descriptors, and process parameters, so feature
vectors align across every experiment record regardless of which specific raw
materials were used.

Feature-set versioning: the classic vector is **v1** (``FEATURE_KEYS``).  When
``FORMUMIND_CHEMTOOLS_DESCRIPTOR_FEATURES=true`` and RDKit is available, a
**v2** block of weight-averaged molecular descriptors (``DESCRIPTOR_KEYS``) is
appended.  Both training and prediction call the same helpers, so vectors stay
aligned within a process; models must be retrained after toggling the flag
(the registry retrains from the store on startup, so a restart suffices).
"""
from __future__ import annotations

from .chemistry import amine_epoxy_ratio
from .schemas import Formulation

ROLE_KEYS = [
    "resin", "hardener", "inhibitor", "pigment", "filler", "surfactant",
    "builder", "solvent", "active", "accelerator", "chelant", "additive",
]
DERIVED_KEYS = ["resin_hardener_ratio", "waterborne", "total_solids"]
PROCESS_KEYS = ["cure_temperature_c"]

# v1 — stable classic feature set (never changes order or length).
FEATURE_KEYS: list[str] = ROLE_KEYS + DERIVED_KEYS + PROCESS_KEYS

# v2 — opt-in weight-averaged molecular descriptors (RDKit via chemtools).
DESCRIPTOR_KEYS = [
    "desc_mol_wt", "desc_logp", "desc_tpsa", "desc_hbd", "desc_hba", "desc_arom_rings",
]

FEATURE_SET_V1 = "v1"
FEATURE_SET_V2 = "v2-desc"


def _descriptor_features_enabled() -> bool:
    from ..config import get_settings

    return bool(get_settings().chemtools_descriptor_features)


def feature_set_version() -> str:
    return FEATURE_SET_V2 if _descriptor_features_enabled() else FEATURE_SET_V1


def active_feature_keys() -> list[str]:
    """Feature keys for the currently configured feature-set version."""
    if _descriptor_features_enabled():
        return FEATURE_KEYS + DESCRIPTOR_KEYS
    return list(FEATURE_KEYS)


def _descriptor_block(form: Formulation) -> dict[str, float]:
    """Weight-averaged molecular descriptors over ingredients with a SMILES.

    Ingredients whose SMILES is missing or unparseable simply don't contribute;
    when nothing resolves the block is all zeros, so vector length and order
    stay identical for every record within a feature-set version.
    """
    from ..services import chemtools

    totals = {k: 0.0 for k in DESCRIPTOR_KEYS}
    weight_sum = 0.0
    for ing in form.ingredients:
        if not ing.smiles or ing.weight_pct <= 0:
            continue
        desc = chemtools.mol_descriptors(ing.smiles)
        if not desc:
            continue
        w = float(ing.weight_pct)
        weight_sum += w
        for name in chemtools.DESCRIPTOR_NAMES:
            totals[f"desc_{name}"] += desc[name] * w
    if weight_sum <= 0:
        return totals
    return {k: round(v / weight_sum, 4) for k, v in totals.items()}


def featurize(form: Formulation, process: dict | None = None) -> dict[str, float]:
    """Return an ordered feature dict for a formulation."""
    process = process or {}
    roles = {k: 0.0 for k in ROLE_KEYS}
    for ing in form.ingredients:
        key = ing.role if ing.role in roles else "additive"
        roles[key] += ing.weight_pct

    ratio = amine_epoxy_ratio(form) or 0.0
    waterborne = 1.0 if any(
        i.name == "Deionized water" and i.weight_pct > 30 for i in form.ingredients
    ) else 0.0
    total_solids = max(0.0, 100.0 - roles["solvent"])

    feats: dict[str, float] = dict(roles)
    feats["resin_hardener_ratio"] = float(ratio)
    feats["waterborne"] = waterborne
    feats["total_solids"] = round(total_solids, 4)
    feats["cure_temperature_c"] = float(process.get("cure_temperature_c", 0.0))
    if _descriptor_features_enabled():
        feats.update(_descriptor_block(form))
    return feats


def vector(form: Formulation, process: dict | None = None) -> list[float]:
    feats = featurize(form, process)
    return [feats[k] for k in active_feature_keys()]

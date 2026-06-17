"""Rheological and glass-transition property estimation (v0.5).

Provides CPU-only, zero-dependency empirical formulas:
  - Fox equation for multi-component polymer Tg
  - Mooney-Rivlin-style relative viscosity from pigment volume loading
  - Viscoelastic index (dimensionless composite quality indicator)

All functions degrade gracefully: if tg_k data is absent for any component
the function returns None rather than fabricating a value.
"""
from __future__ import annotations

from ..domain.schemas import Formulation

_POLYMER_ROLES = {"resin", "hardener"}
_PIGMENT_ROLES = {"pigment", "filler", "inhibitor"}

# Random-close-packing upper limit for Mooney model
_PHI_MAX = 0.64

# Room temperature reference (K) for viscoelastic index
_T_AMBIENT_K = 298.15


def fox_tg(form: Formulation) -> float | None:
    """Fox equation for a multi-component polymer blend (K).

    1/Tg_mix = Σ(w_i / Tg_i)

    Only polymer-role components (resin, hardener) with known tg_k contribute.
    Returns None when any polymer component lacks tg_k data.
    """
    from ..domain.knowledge import RAW_MATERIALS

    inv_tg = 0.0
    total_w = 0.0
    for ing in form.ingredients:
        if ing.role not in _POLYMER_ROLES:
            continue
        if ing.weight_pct <= 0:
            continue
        tg = RAW_MATERIALS.get(ing.name, {}).get("tg_k")
        if tg is None:
            return None  # graceful degradation: missing data → skip prediction
        inv_tg += ing.weight_pct / tg
        total_w += ing.weight_pct

    if total_w <= 0 or inv_tg <= 0:
        return None
    tg_mix_k = total_w / inv_tg
    return round(tg_mix_k, 2)


def fox_tg_celsius(form: Formulation) -> float | None:
    """Fox equation result in °C (None when data is missing)."""
    tg_k = fox_tg(form)
    return round(tg_k - 273.15, 2) if tg_k is not None else None


def mooney_viscosity(form: Formulation) -> float | None:
    """Mooney relative viscosity from pigment volume fraction.

    η_r = exp(2.5·φ / (1 - φ/φ_max))

    φ is derived from the PVC descriptor. Returns None when PVC is zero or
    when φ > 0.5 (outside the model's reliable range).
    """
    try:
        from ..domain.chemistry import pvc

        pvc_val = pvc(form)
    except Exception:
        return None

    if pvc_val <= 0:
        return None
    phi = pvc_val / 100.0  # convert % to fraction
    if phi >= _PHI_MAX:
        return None  # outside Mooney model range
    if phi > 0.5:
        return None  # model unreliable above 50 vol%
    import math

    eta_r = math.exp(2.5 * phi / (1.0 - phi / _PHI_MAX))
    return round(eta_r, 3)


def viscoelastic_index(form: Formulation) -> float:
    """Dimensionless viscoelastic quality index [0–1].

    Combines two signals:
      - How far the film Tg sits above ambient (higher = better barrier)
      - PVC/CPVC ratio (below 1 = good barrier film)

    When Tg data is unavailable the index is estimated from PVC alone.
    """
    from ..domain.chemistry import cpvc, pvc

    score = 0.5  # neutral default

    tg_k = fox_tg(form)
    if tg_k is not None:
        # Tg above ambient → better thermoset cure quality (capped at 80 K margin)
        delta_t = max(0.0, min(80.0, tg_k - _T_AMBIENT_K))
        score = delta_t / 80.0

    pvc_val = pvc(form)
    if pvc_val > 0:
        cpvc_val = cpvc(form)
        if cpvc_val:
            ratio = pvc_val / cpvc_val
            # ratio < 1 is good (dense film); contribution mapped [0, 1]
            pvc_contribution = max(0.0, 1.0 - ratio)
            score = (score + pvc_contribution) / 2.0

    return round(max(0.0, min(1.0, score)), 3)

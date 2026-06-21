"""Colorimetry service — CIELAB color estimate and CIEDE2000 color difference.

Uses ``colour-science`` for the standards-grade ΔE₀₀ computation when installed;
otherwise falls back to the CIE76 Euclidean distance in L*a*b* so the metric is
always available offline. The formulation color is estimated as the
pigment-volume-weighted mean of the particulate pigments' tabulated CIELAB
values (a screening-level approximation, not a spectral simulation).
"""
from __future__ import annotations

from ..domain.chemistry import _PIGMENT_ROLES, _density_gcm3
from ..domain.schemas import Formulation

# Reference white for the ΔE comparison (perfect diffuse white).
WHITE_LAB: tuple[float, float, float] = (100.0, 0.0, 0.0)


def _colour_available() -> bool:
    try:
        import colour  # noqa: F401

        return True
    except Exception:
        return False


def delta_e_2000(
    lab1: tuple[float, float, float], lab2: tuple[float, float, float]
) -> float:
    """CIEDE2000 color difference ΔE₀₀ between two CIELAB triples.

    Falls back to the CIE76 Euclidean distance when ``colour-science`` is absent.
    """
    if _colour_available():
        try:  # pragma: no cover - requires colour-science
            import numpy as np
            from colour.difference import delta_E_CIE2000

            return round(float(delta_E_CIE2000(np.array(lab1), np.array(lab2))), 3)
        except Exception:
            pass
    return round(sum((a - b) ** 2 for a, b in zip(lab1, lab2)) ** 0.5, 3)


def mixture_lab(form: Formulation) -> tuple[float, float, float] | None:
    """Pigment-volume-weighted CIELAB color of a formulation.

    Returns None when no pigment in the formulation has a tabulated ``lab``
    value, so callers can skip color reporting rather than guess.
    """
    from ..domain.knowledge import RAW_MATERIALS

    L = a = b = vol_total = 0.0
    for ing in form.ingredients:
        if ing.role not in _PIGMENT_ROLES:
            continue
        lab = RAW_MATERIALS.get(ing.name, {}).get("lab")
        if not lab:
            continue
        vol = ing.weight_pct / _density_gcm3(ing.name, ing.role)
        L += lab[0] * vol
        a += lab[1] * vol
        b += lab[2] * vol
        vol_total += vol
    if vol_total <= 0:
        return None
    return (round(L / vol_total, 2), round(a / vol_total, 2), round(b / vol_total, 2))


def color_metrics(form: Formulation) -> dict[str, float]:
    """Color descriptors for a formulation: ``lab_L/a/b`` and ΔE₀₀ vs white.

    Returns an empty dict when the formulation has no color-bearing pigment,
    leaving the predictor output unchanged.
    """
    lab = mixture_lab(form)
    if lab is None:
        return {}
    return {
        "lab_L": lab[0],
        "lab_a": lab[1],
        "lab_b": lab[2],
        "delta_e": delta_e_2000(lab, WHITE_LAB),
    }

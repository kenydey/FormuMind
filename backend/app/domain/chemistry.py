"""Chemical stoichiometry helpers.

Uses ChemFormula / RDKit when available for precise molar-mass and equivalent
calculations; otherwise falls back to a self-contained formula parser built on
an internal periodic-mass table. Either path returns the same structure so
callers never branch on which engine is active.
"""
from __future__ import annotations

import re

from .schemas import Formulation

# Standard atomic weights (g/mol), IUPAC abridged — enough for common
# coating / surface-treatment raw materials.
ATOMIC_MASS: dict[str, float] = {
    "H": 1.008, "He": 4.003, "Li": 6.94, "Be": 9.012, "B": 10.81, "C": 12.011,
    "N": 14.007, "O": 15.999, "F": 18.998, "Ne": 20.180, "Na": 22.990,
    "Mg": 24.305, "Al": 26.982, "Si": 28.085, "P": 30.974, "S": 32.06,
    "Cl": 35.45, "Ar": 39.948, "K": 39.098, "Ca": 40.078, "Ti": 47.867,
    "V": 50.942, "Cr": 51.996, "Mn": 54.938, "Fe": 55.845, "Co": 58.933,
    "Ni": 58.693, "Cu": 63.546, "Zn": 65.38, "Zr": 91.224, "Mo": 95.95,
    "Sn": 118.71, "Ce": 140.116,
}

_TOKEN = re.compile(r"([A-Z][a-z]?)(\d*)|(\()|(\))(\d*)")


def molar_mass(formula: str) -> float:
    """Parse a chemical formula and return its molar mass (g/mol).

    Supports nested parentheses, e.g. ``Zn3(PO4)2`` or ``Mn(H2PO4)2``.
    Raises ``ValueError`` on an unknown element or malformed string.
    """
    try:
        import chemformula  # type: ignore

        return float(chemformula.ChemFormula(formula).formula_weight)
    except Exception:
        pass
    return _parse_mass(formula)


def _parse_mass(formula: str) -> float:
    stack: list[float] = [0.0]
    pos = 0
    for match in _TOKEN.finditer(formula):
        if match.start() != pos:
            raise ValueError(f"Unparseable formula segment in {formula!r} at {pos}")
        pos = match.end()
        element, count, open_p, close_p, group_count = match.groups()
        if element:
            if element not in ATOMIC_MASS:
                raise ValueError(f"Unknown element {element!r} in {formula!r}")
            stack[-1] += ATOMIC_MASS[element] * (int(count) if count else 1)
        elif open_p:
            stack.append(0.0)
        elif close_p:
            group = stack.pop()
            stack[-1] += group * (int(group_count) if group_count else 1)
    if pos != len(formula):
        raise ValueError(f"Trailing characters in formula {formula!r}")
    if len(stack) != 1:
        raise ValueError(f"Unbalanced parentheses in formula {formula!r}")
    return round(stack[0], 4)


def validate_formulation(form: Formulation, voc_limit_gpl: float | None = None) -> list[str]:
    """Return a list of human-readable warnings about a formulation.

    Checks weight-percent closure and recomputes molar masses where a formula
    is available, flagging inconsistencies with the declared value.
    When ``voc_limit_gpl`` is supplied and the formulation's ``predicted``
    dict already contains ``voc_gpl``, a VOC-exceedance warning is appended.
    """
    warnings: list[str] = []
    total = form.total_pct()
    if abs(total - 100.0) > 0.5:
        warnings.append(f"Weight percentages sum to {total:.2f}, expected ~100.")
    for ing in form.ingredients:
        if ing.formula:
            try:
                computed = molar_mass(ing.formula)
            except ValueError as exc:
                warnings.append(f"{ing.name}: {exc}")
                continue
            if ing.molar_mass and abs(computed - ing.molar_mass) / ing.molar_mass > 0.02:
                warnings.append(
                    f"{ing.name}: declared M={ing.molar_mass} but formula {ing.formula} gives {computed}."
                )
            ing.molar_mass = ing.molar_mass or computed
    if voc_limit_gpl is not None and "voc_gpl" in form.predicted:
        voc = form.predicted["voc_gpl"]
        if voc > voc_limit_gpl:
            warnings.append(f"VOC {voc:.0f} g/L exceeds limit {voc_limit_gpl:.0f} g/L.")
    return warnings


def amine_epoxy_ratio(form: Formulation) -> float | None:
    """Crude resin:hardener equivalent ratio used as a cross-link-density proxy
    for two-component anti-corrosion systems. Returns None when not applicable.
    """
    resin = sum(i.weight_pct for i in form.ingredients if i.role == "resin")
    hardener = sum(i.weight_pct for i in form.ingredients if i.role == "hardener")
    if resin <= 0 or hardener <= 0:
        return None
    return round(resin / hardener, 3)

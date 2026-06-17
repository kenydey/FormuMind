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


# ── Pigment Volume Concentration (PVC / CPVC / solids-by-volume) ──────────────
# Core coating descriptors that the empirical predictor lacked. They are
# volume-based, so each component's mass fraction is divided by its density to
# get a relative volume. Densities come from the knowledge base when available;
# otherwise role-based nominal values keep the calculation usable offline.

# Particulate solids that count toward the pigment volume (pigments, extenders,
# and particulate anti-corrosive pigments such as zinc phosphate/molybdate).
_PIGMENT_ROLES = {"pigment", "filler", "inhibitor"}
_VOLATILE_ROLES = {"solvent"}  # evaporate from the dry film
_ROLE_DENSITY_GCM3: dict[str, float] = {
    "pigment": 3.8, "filler": 2.6, "inhibitor": 3.1, "resin": 1.1,
    "hardener": 1.0, "active": 1.5, "accelerator": 1.5, "builder": 2.1,
    "surfactant": 1.0, "chelant": 1.5, "solvent": 0.95, "additive": 1.1,
}


def _density_gcm3(name: str, role: str) -> float:
    """Component density (g/cm³): knowledge-base value, then role nominal."""
    from .knowledge import RAW_MATERIALS

    spec = RAW_MATERIALS.get(name, {})
    rho = spec.get("density_gcm3")
    if rho and rho > 0:
        return float(rho)
    return _ROLE_DENSITY_GCM3.get(role, 1.2)


def _component_volumes(form: Formulation) -> tuple[float, float, float]:
    """Return (pigment_volume, binder_solids_volume, volatile_volume) — relative
    volumes (mass-fraction / density) over the formulation."""
    pigment = binder = volatile = 0.0
    for ing in form.ingredients:
        vol = ing.weight_pct / _density_gcm3(ing.name, ing.role)
        if ing.role in _PIGMENT_ROLES:
            pigment += vol
        elif ing.role in _VOLATILE_ROLES:
            volatile += vol
        else:
            binder += vol
    return pigment, binder, volatile


def pvc(form: Formulation) -> float:
    """Pigment Volume Concentration (%): pigment volume / dry-film volume."""
    pigment, binder, _ = _component_volumes(form)
    denom = pigment + binder
    return round(100.0 * pigment / denom, 2) if denom > 0 else 0.0


def solids_by_volume(form: Formulation) -> float:
    """Solids by Volume (%): non-volatile volume / total wet volume."""
    pigment, binder, volatile = _component_volumes(form)
    total = pigment + binder + volatile
    return round(100.0 * (pigment + binder) / total, 2) if total > 0 else 0.0


def cpvc(form: Formulation) -> float | None:
    """Critical Pigment Volume Concentration (%) via the Asbeck–Van Loo formula.

    CPVC = 1 / (1 + OA·ρ_p / 93.5), where OA is the pigment-blend oil absorption
    (g oil / 100 g pigment) and ρ_p its mean density. Returns None when oil
    absorption is unknown for the pigment blend (so the descriptor degrades
    gracefully rather than reporting a fabricated value).
    """
    from .knowledge import RAW_MATERIALS

    oa_w = rho_w = mass = 0.0
    for ing in form.ingredients:
        if ing.role not in _PIGMENT_ROLES:
            continue
        oa = RAW_MATERIALS.get(ing.name, {}).get("oil_absorption")
        if oa is None:
            return None
        oa_w += oa * ing.weight_pct
        rho_w += _density_gcm3(ing.name, ing.role) * ing.weight_pct
        mass += ing.weight_pct
    if mass <= 0:
        return None
    oa_avg, rho_avg = oa_w / mass, rho_w / mass
    cpvc_frac = 1.0 / (1.0 + oa_avg * rho_avg / 93.5)
    return round(100.0 * cpvc_frac, 2)


# ── v0.5: Compatibility & Safety Checker ─────────────────────────────────────

# Roles that indicate strong acid or strong base character
_ACID_INGREDIENT_NAMES = {"Phosphoric acid"}
_BASE_INGREDIENT_NAMES = {"Sodium hydroxide", "Sodium metasilicate"}

# EU REACH SVHC candidates relevant to metal-treatment coatings
_SVHC_NAMES = {
    "Zinc molybdate",      # molybdate, potential SVHC
    "Cerium nitrate",      # rare earth nitrate
    "Sodium nitrite",      # nitrite accelerator
}

# EU VOC Directive 2010/75/EU product categories (simplified)
_WATERBORNE_THRESHOLD_GPL = 250.0
_HIGHSOLIDS_SOLVENT_MAX_GPL = 80.0


def check_acid_base_conflict(form: Formulation) -> list[str]:
    """Warn if strong acid and strong base co-exist in the same formulation."""
    has_acid = any(i.name in _ACID_INGREDIENT_NAMES for i in form.ingredients if i.weight_pct > 0.1)
    has_base = any(i.name in _BASE_INGREDIENT_NAMES for i in form.ingredients if i.weight_pct > 0.1)
    if has_acid and has_base:
        acids = [i.name for i in form.ingredients if i.name in _ACID_INGREDIENT_NAMES]
        bases = [i.name for i in form.ingredients if i.name in _BASE_INGREDIENT_NAMES]
        return [f"Acid-base conflict: {', '.join(acids)} vs {', '.join(bases)} — verify pH stability."]
    return []


def check_svhc(form: Formulation) -> list[str]:
    """List ingredients that are EU REACH SVHC candidates (knowledge-base + name list)."""
    from .knowledge import RAW_MATERIALS

    found = []
    for ing in form.ingredients:
        if ing.weight_pct <= 0:
            continue
        spec = RAW_MATERIALS.get(ing.name, {})
        if spec.get("svhc") or ing.name in _SVHC_NAMES:
            found.append(ing.name)
    if found:
        return [f"REACH SVHC candidate(s) detected: {', '.join(found)}. Confirm regulatory status."]
    return []


def check_voc_category(form: Formulation, voc_gpl: float | None = None) -> str:
    """Classify formulation under EU VOC Directive 2010/75/EU (simplified).

    Returns one of: "waterborne", "high-solids", "solventborne", "solvent-free".
    Uses pre-computed voc_gpl if supplied; otherwise looks at solvent roles.
    """
    water_pct = sum(i.weight_pct for i in form.ingredients if i.name == "Deionized water")
    solvent_pct = sum(i.weight_pct for i in form.ingredients
                      if i.role == "solvent" and i.name != "Deionized water")

    if water_pct > 40 and (voc_gpl is None or voc_gpl < _WATERBORNE_THRESHOLD_GPL):
        return "waterborne"
    if solvent_pct < 5:
        return "solvent-free"
    if voc_gpl is not None and voc_gpl <= _HIGHSOLIDS_SOLVENT_MAX_GPL:
        return "high-solids"
    return "solventborne"


def full_safety_check(
    form: Formulation,
    voc_gpl: float | None = None,
    voc_limit_gpl: float | None = None,
) -> list[str]:
    """Aggregate all safety/compliance warnings, extending validate_formulation().

    Returns a list of human-readable warning strings (empty = no issues).
    """
    warnings: list[str] = []
    warnings.extend(check_acid_base_conflict(form))
    warnings.extend(check_svhc(form))

    # VOC limit check (only when both limit and measured value are known)
    if voc_gpl is not None and voc_limit_gpl is not None and voc_gpl > voc_limit_gpl:
        warnings.append(
            f"VOC {voc_gpl:.0f} g/L exceeds limit {voc_limit_gpl:.0f} g/L "
            f"(category: {check_voc_category(form, voc_gpl)})."
        )
    return warnings

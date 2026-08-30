"""Acid-stability screening for formulations in acidic working baths.

The core use case is **autodeposition (self-depositing) coating baths**, which
operate at pH 2-4: the dispersed polymer must survive the bulk bath and only
coagulate at the metal surface. This module provides a *deterministic,
zero-LLM* rule engine that flags ingredient combinations that are likely to
destabilise (coagulate / hydrolyse / gas-evolve) under acidic conditions.

Design
------
* Two independent axes:

  1. **Dispersion tolerance** — every aqueous resin/dispersion declares an
     ``acid_tolerance_ph`` (lowest bath pH it survives) in the raw-material
     catalog. If the working bath pH is below that tolerance the dispersion is
     flagged as likely to coagulate in the bath.
  2. **Composition rules** — hard, chemistry-derived combination rules that
     hold regardless of the catalog metadata:

     * strong alkali + acid → neutralisation / pH spike (bath instability)
     * carbonate / bicarbonate filler + acid → CO₂ gas evolution (foaming)
     * zinc dust / reactive metal + acid → hydrogen evolution
     * acid-cured amino resin in low-pH bath → protonation-driven instability
       (amine neutraliser fails below its pKa)

* Soft by default: ``status="warn"`` entries lower ranking and surface a
  reason; only rules marked ``hard=True`` (e.g. strong acid + strong base in
  the same bath) flip the verdict to ``infeasible``.
* Unknown materials / missing metadata are **never** treated as a violation —
  the engine constrains only what the catalog or rules actually know
  (same philosophy as ``kg_chemical_check``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..domain.knowledge import RAW_MATERIALS
from ..domain.schemas import Formulation

logger = logging.getLogger(__name__)

# Default autodeposition / acidic-bath working window.
_DEFAULT_BATH_PH = 3.0

# Role of aqueous polymer dispersions that need acid tolerance.
_RESIN_ROLES = {"resin", "hardener"}

# ── Composition rules (name-prefix + role based, chemistry-derived) ─────────

# Strong alkali names whose co-presence with an acid is a hard infeasibility
# (they buffer the bath far out of the acidic window and can react violently).
_STRONG_ALKALI_PREFIXES = ("Sodium hydroxide", "Potassium hydroxide")
_STRONG_ALKALI_EXACT = {"Sodium metasilicate", "Sodium tripolyphosphate"}

# Carbonate/bicarbonate fillers — gas evolution (CO₂) under acid.
_CARBONATE_SUBSTRINGS = ("carbonate", "bicarbonate", "chalk", "limestone")

# Reactive metals that evolve hydrogen under acid.
_REACTIVE_METALS = {"Zinc dust", "Zinc oxide", "Aluminum powder", "Aluminium powder"}

# Acid-cured / amine-neutralised dispersions that fail at low pH.
_AMINE_NEUTRALISED_SUBSTRINGS = ("amine", "ammonia")


@dataclass
class AcidStabilityResult:
    stable: bool
    status: str = "pass"  # pass | warn | infeasible
    reasons: list[str] = field(default_factory=list)
    bath_ph: float | None = None

    def __bool__(self) -> bool:
        return self.stable


def _resolve_bath_ph(form: Formulation, bath_ph: float | None) -> float | None:
    """Prefer the explicit bath pH; fall back to the requirement's ph_target
    carried on the formulation's warnings-free fields (not stored), then the
    default autodeposition window."""
    if bath_ph is not None:
        return float(bath_ph)
    predicted = form.predicted or {}
    ph = predicted.get("ph")
    if ph is not None:
        return float(ph)
    return _DEFAULT_BATH_PH


def _acid_tolerance_violations(form: Formulation, bath_ph: float) -> list[str]:
    """Dispersion tolerance axis: aqueous resins whose acid_tolerance_ph is
    above the working bath pH."""
    out: list[str] = []
    for ing in form.ingredients:
        if ing.role not in _RESIN_ROLES:
            continue
        spec = RAW_MATERIALS.get(ing.name, {})
        tol = spec.get("acid_tolerance_ph")
        if tol is None:
            continue  # unknown tolerance → no constraint
        if float(tol) > bath_ph + 0.05:
            out.append(
                f"{ing.name}: 酸耐受下限 pH {float(tol):.1f} 高于工作浴 pH {bath_ph:.1f}"
                f"（浴中可能破乳/凝聚）"
            )
    return out


def _composition_violations(form: Formulation) -> list[tuple[bool, str]]:
    """Composition-rule axis. Returns [(hard, reason), ...]."""
    names = [i.name for i in form.ingredients if i.weight_pct > 0.05]
    lowered = {n.lower() for n in names}
    out: list[tuple[bool, str]] = []

    # Strong alkali + (any acid) → hard.
    alkali = [
        n for n in names
        if n in _STRONG_ALKALI_EXACT or any(n.startswith(p) for p in _STRONG_ALKALI_PREFIXES)
    ]
    if alkali:
        out.append((True, f"强碱 {', '.join(alkali)} 与酸性浴 pH 冲突（中和放热，浴失控）"))

    # Carbonate filler + acid → hard (gas evolution).
    carbonates = [n for n in names if any(s in n.lower() for s in _CARBONATE_SUBSTRINGS)]
    if carbonates:
        out.append((True, f"碳酸盐填料 {', '.join(carbonates)} 在酸性浴中释放 CO₂（起泡）"))

    # Reactive metal + acid → hard (hydrogen).
    metals = [n for n in names if n in _REACTIVE_METALS]
    if metals:
        out.append((True, f"活泼金属 {', '.join(metals)} 在酸性浴中析氢（安全与膜层缺陷风险）"))

    # Amine-neutralised binder in a strongly acidic bath → warn.
    if any(any(s in n.lower() for s in _AMINE_NEUTRALISED_SUBSTRINGS) for n in names):
        out.append((False, "含胺中和剂组分在低 pH 浴中可能质子化失效（建议核实乳液酸耐受性）"))

    return out


def check_acid_stability(
    form: Formulation,
    bath_ph: float | None = None,
) -> AcidStabilityResult:
    """Deterministic acid-stability screen for a formulation in an acidic bath.

    ``bath_ph`` may be given explicitly; otherwise the formulation's predicted
    ``ph`` is used; otherwise the default autodeposition window (3.0) applies.
    """
    bath_ph = _resolve_bath_ph(form, bath_ph)
    reasons: list[str] = []
    hard_hit = False

    if bath_ph is not None:
        reasons.extend(_acid_tolerance_violations(form, bath_ph))
    for hard, reason in _composition_violations(form):
        reasons.append(reason)
        hard_hit = hard_hit or hard

    if not reasons:
        return AcidStabilityResult(stable=True, status="pass", bath_ph=bath_ph)
    status = "infeasible" if hard_hit else "warn"
    return AcidStabilityResult(
        stable=not hard_hit,
        status=status,
        reasons=reasons,
        bath_ph=bath_ph,
    )

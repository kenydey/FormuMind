"""Variable-topology formulation representation.

``reconstruct.formulation_from_factors`` can only rescale the wt% of
ingredients that already exist in a hardcoded baseline template — it cannot
add, remove, or swap one. That single constraint is why the platform has no
inverse design (target → composition is unreachable when composition is
fixed), no material substitution (a swap cannot be simulated), and only a
post-hoc Pareto filter rather than a Pareto search.

A genome makes ingredient *choice* a first-class variable:

    Slot   = (role, material, weight_pct, unit, locked)
    Genome = domain + slots

``locked`` marks a slot the user has pinned, so search leaves it alone.

``unit`` is carried per slot deliberately. The g/L → wt% conversion for
surface-treatment baths is currently recovered by looking a lever up *by
ingredient name* (``reconstruct.py``); once a slot's material can change, that
lookup no longer finds anything and the bath silently lands 10× off.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from . import knowledge
from .schemas import Formulation, Ingredient, ProductDomain, Requirement

# Dilute aqueous baths: g/L ≈ wt% × 10 (density ~1 g/mL). Mirrors
# reconstruct._G_PER_L_TO_WT_PCT — kept in sync deliberately.
_G_PER_L_TO_WT_PCT = 0.1

# Roles the optimizer should not swap out: they are carriers or bulk fillers
# whose identity is set by the formulation type, not by search.
FIXED_ROLES = frozenset({"solvent", "pigment", "filler", "additive"})


class UnknownMaterialError(ValueError):
    """A slot names a material that isn't in the catalog.

    Raised rather than skipped on purpose: ``formulation_from_factors``
    silently drops unknown factor names, which makes a half-wired search look
    like it is running while doing nothing at all.
    """


class Slot(BaseModel):
    """One occupied position in a formulation."""

    role: str
    material: str
    weight_pct: float = Field(ge=0)
    unit: str = "wt%"
    locked: bool = False

    def to_wt_pct(self) -> float:
        """Weight percent, converting from the slot's own unit."""
        if self.unit == "g/L":
            return round(self.weight_pct * _G_PER_L_TO_WT_PCT, 4)
        return round(self.weight_pct, 4)

    @staticmethod
    def from_wt_pct(weight_pct: float, unit: str) -> float:
        """Inverse of ``to_wt_pct`` — express a wt% amount in ``unit``.

        Slots hold their amount in their own unit so search operates on the
        natural range (surface-treatment levers are specified 5–30 g/L, not
        0.5–3 wt%). Conversion has to be symmetric or the round trip lands 10×
        off for every bath formulation.
        """
        if unit == "g/L":
            return round(weight_pct / _G_PER_L_TO_WT_PCT, 4)
        return round(weight_pct, 4)


class FormulationGenome(BaseModel):
    """A formulation as a searchable set of (material, amount) choices."""

    domain: ProductDomain
    slots: list[Slot] = Field(default_factory=list)
    name: str = ""
    rationale: str = ""

    def materials(self) -> list[str]:
        return [s.material for s in self.slots]

    def swappable(self) -> list[Slot]:
        """Slots search is allowed to re-materialise."""
        return [s for s in self.slots if not s.locked and s.role not in FIXED_ROLES]

    def with_material(self, index: int, material: str) -> FormulationGenome:
        """Copy with one slot's material replaced — the substitution primitive."""
        if not 0 <= index < len(self.slots):
            raise IndexError(f"slot index {index} out of range")
        slots = [s.model_copy(deep=True) for s in self.slots]
        slots[index].material = material
        slots[index].role = _role_of(material) or slots[index].role
        return self.model_copy(update={"slots": slots})


def _role_of(material: str) -> str:
    spec = knowledge.RAW_MATERIALS.get(material) or {}
    return str(spec.get("role") or "")


def genome_from_formulation(
    form: Formulation,
    *,
    units: dict[str, str] | None = None,
) -> FormulationGenome:
    """Formulation → genome. ``units`` maps ingredient name → lever unit."""
    units = units or {}
    slots = []
    for ing in form.ingredients:
        unit = units.get(ing.name, "wt%")
        slots.append(
            Slot(
                role=ing.role or _role_of(ing.name) or "additive",
                material=ing.name,
                # Ingredient.weight_pct is always wt%; the slot holds it in its
                # own unit, so convert on the way in.
                weight_pct=Slot.from_wt_pct(ing.weight_pct, unit),
                unit=unit,
            )
        )
    return FormulationGenome(
        domain=form.domain, slots=slots, name=form.name, rationale=form.rationale
    )


def formulation_from_genome(
    req: Requirement | ProductDomain,
    genome: FormulationGenome,
    *,
    strict: bool = True,
) -> Formulation:
    """Genome → Formulation, renormalised to 100%.

    Unlike the factor path this can add, drop, or swap ingredients: the
    composition is whatever the slots say it is.

    ``strict`` (default) raises on a material the catalog doesn't know, so a
    mis-wired search fails loudly instead of quietly producing the baseline.
    """
    domain = req.domain if isinstance(req, Requirement) else req
    ings: list[Ingredient] = []
    for slot in genome.slots:
        known = slot.material in knowledge.RAW_MATERIALS
        if not known and strict:
            raise UnknownMaterialError(
                f"slot material not in catalog: {slot.material!r}"
            )
        wt = slot.to_wt_pct()
        if wt <= 0:
            continue
        ing = knowledge.ingredient(slot.material, wt)
        if not known:
            # Tolerant mode: keep the slot but mark the role we were told.
            ing.role = slot.role or ing.role
        ings.append(ing)
    if not ings:
        raise ValueError("genome produced no ingredients")
    name = genome.name or f"{domain.value} (genome)"
    # Reuses the existing solvent-absorbing renormaliser so weight closure and
    # the choice of which component absorbs the residual stay identical to the
    # template path.
    return knowledge._balanced(name, domain, ings, genome.rationale)


def candidates_for_role(
    role: str,
    req: Requirement | None = None,
    *,
    include_unavailable: bool = False,
) -> list[str]:
    """Catalog materials that could occupy a slot of ``role``.

    Filtered by carrier compatibility (a solvent-borne hardener is not a
    candidate in a waterborne system) and by supply status, so a discontinued
    material is never proposed. Order follows the catalog: seed first.
    """
    waterborne = _requirement_is_waterborne(req) if req is not None else None
    out: list[str] = []
    for name, spec in knowledge.RAW_MATERIALS.items():
        if spec.get("role") != role:
            continue
        if not include_unavailable and spec.get("availability") == "discontinued":
            continue
        if waterborne is not None and not _carrier_compatible(spec, waterborne):
            continue
        out.append(name)
    return out


def _carrier_compatible(spec: dict, waterborne: bool) -> bool:
    carrier = spec.get("carrier", "both")
    if carrier == "both":
        return True
    return carrier == ("aqueous" if waterborne else "solvent")


def _requirement_is_waterborne(req: Requirement) -> bool:
    """Same rule the anticorrosion template uses to pick its resin/solvent."""
    voc_limit = req.voc_limit_gpl if req.voc_limit_gpl is not None else 9999.0
    cure_temp = req.cure_temperature_c if req.cure_temperature_c is not None else 80.0
    return voc_limit < 250 or cure_temp < 60

"""Build baybe SearchSpace from FormuMind requirement + DOE factors."""
from __future__ import annotations

from ....domain.project_spec import normalize_constraints
from ....domain.schemas import DOEFactor, Requirement


def _max_lever_sum(factors: list[DOEFactor]) -> float:
    """Upper bound on sum of lever wt% (solvent absorbs the remainder)."""
    lever_factors = [f for f in factors if f.unit == "wt%"]
    if not lever_factors:
        return 100.0
    return min(100.0, sum(f.high for f in lever_factors))


def _is_ingredient_factor(name: str) -> bool:
    """True when the factor is a material amount rather than a process knob.

    Factor names share one namespace with ingredient names, and the keyword
    tests below are substring matches: "ph" occurs inside "Phosphoric acid",
    "Zinc phosphate", "Manganese dihydrogen phosphate" and "Bisphenol-A epoxy
    (DGEBA)". Without this guard a ph_target silently collapsed their wt%
    range — Phosphoric acid went from [3, 14] to [3, 4.5] on a steel
    phosphating bath, cutting 87% of the search space with no warning.

    A pH or VOC target constrains the process, never a component's dosage.
    """
    from ....domain.knowledge import RAW_MATERIALS

    return name in RAW_MATERIALS


def apply_requirement_bounds(req: Requirement, factors: list[DOEFactor]) -> list[DOEFactor]:
    """Tighten factor bounds from Requirement constraints before BayBE search."""
    constraints = normalize_constraints(req)
    adjusted: list[DOEFactor] = []
    for factor in factors:
        low, high = float(factor.low), float(factor.high)
        name_lower = factor.name.lower()
        process_knob = not _is_ingredient_factor(factor.name)

        if (
            process_knob
            and req.cure_temperature_c is not None
            and "cure" in name_lower
            and "temp" in name_lower
        ):
            high = min(high, float(req.cure_temperature_c))
        if process_knob and req.ph_target is not None and "ph" in name_lower:
            target = float(req.ph_target)
            low = max(low, target - 1.5)
            high = min(high, target + 1.5)

        voc_label = constraints.get("VOC 上限")
        if process_knob and voc_label is not None and "voc" in name_lower:
            high = min(high, float(voc_label))

        if high <= low:
            high = low + 1e-3
        adjusted.append(factor.model_copy(update={"low": round(low, 4), "high": round(high, 4)}))
    return adjusted


def build_searchspace(req: Requirement, factors: list[DOEFactor]):
    from baybe.constraints import ContinuousLinearConstraint
    from baybe.parameters import NumericalContinuousParameter
    from baybe.searchspace import SearchSpace

    bounded = apply_requirement_bounds(req, factors)
    parameters = [
        NumericalContinuousParameter(name=f.name, bounds=(float(f.low), float(f.high)))
        for f in bounded
    ]
    lever_names = [f.name for f in bounded if f.unit == "wt%"]
    constraints = []
    if len(lever_names) >= 2:
        constraints.append(
            ContinuousLinearConstraint(
                parameters=lever_names,
                operator="<=",
                coefficients=tuple(1.0 for _ in lever_names),
                rhs=_max_lever_sum(bounded),
            )
        )
    return SearchSpace.from_product(parameters=parameters, constraints=constraints or None)


# Search spaces are frozen inside a serialized campaign_state (parameter names
# and all), so a genome space cannot be loaded from a state built for the
# numeric one. Callers stamp this and fall back to the numeric path on mismatch
# rather than letting Campaign.from_json blow up.
GENOME_SPACE_VERSION = "genome-v1"


def build_genome_searchspace(req: Requirement, genome, candidate_pools: dict[str, list[str]]):
    """Mixed search space where ingredient *choice* is a variable.

    Each swappable slot contributes two parameters: which material occupies it,
    and how much. ``candidate_pools`` maps slot key → allowed material names.

    Material choice is categorical by default. It is upgraded to a
    ``SubstanceParameter`` only when every candidate in that pool has a SMILES,
    because that parameter type encodes molecules as chemical descriptors —
    which is what lets the surrogate generalize *across* materials instead of
    treating them as unrelated labels, and is therefore what makes a predicted
    swap meaningful. Only a third of the seed catalog carries SMILES today, so
    categorical has to remain the fallback.
    """
    from baybe.constraints import ContinuousLinearConstraint
    from baybe.parameters import CategoricalParameter, NumericalContinuousParameter
    from baybe.searchspace import SearchSpace

    from ...domain import knowledge

    parameters: list = []
    wt_names: list[str] = []
    for slot_key, pool in candidate_pools.items():
        if len(pool) > 1:
            parameters.append(_material_parameter(slot_key, pool, knowledge))
        low, high = _slot_bounds(genome, slot_key)
        wt_name = f"wt_{slot_key}"
        parameters.append(NumericalContinuousParameter(name=wt_name, bounds=(low, high)))
        wt_names.append(wt_name)

    if not parameters:
        raise ValueError("genome search space has no parameters")

    constraints = []
    if len(wt_names) >= 2:
        constraints.append(
            ContinuousLinearConstraint(
                parameters=wt_names,
                operator="<=",
                coefficients=tuple(1.0 for _ in wt_names),
                rhs=100.0,
            )
        )
    return SearchSpace.from_product(parameters=parameters, constraints=constraints or None)


def _material_parameter(slot_key: str, pool: list[str], knowledge_mod):
    """SubstanceParameter when the whole pool has SMILES, else categorical."""
    from baybe.parameters import CategoricalParameter

    name = f"mat_{slot_key}"
    smiles = {
        material: (knowledge_mod.RAW_MATERIALS.get(material) or {}).get("smiles")
        for material in pool
    }
    if all(smiles.values()):
        try:
            from baybe.parameters import SubstanceParameter

            return SubstanceParameter(name=name, data=smiles)
        except Exception:
            # Descriptor backends (mordred/rdkit) are optional — degrade to
            # labels rather than failing the whole search.
            pass
    return CategoricalParameter(name=name, values=tuple(pool))


def _slot_bounds(genome, slot_key: str, *, margin: float = 0.3) -> tuple[float, float]:
    """±30% around the slot's current amount, clamped to [0, 100]."""
    for slot in genome.slots:
        if _slot_key(slot) != slot_key:
            continue
        current = float(slot.to_wt_pct())
        low = max(0.0, current * (1.0 - margin))
        high = min(100.0, max(current * (1.0 + margin), low + 1.0))
        return round(low, 4), round(high, 4)
    return 0.0, 100.0


def _slot_key(slot) -> str:
    """Stable parameter-name fragment for a slot (role-based, swap-invariant)."""
    return slot.role or "additive"


def factors_for_requirement(req: Requirement, factors: list[DOEFactor] | None = None) -> list[DOEFactor]:
    if factors is not None:
        return factors
    from ....pipeline.workflow import build_doe_factors

    return build_doe_factors(req)


def factors_from_campaign(campaign, req: Requirement) -> list[DOEFactor]:
    """Use Campaign.lever_snapshot when recommending from a workbench campaign."""
    if campaign is not None and campaign.lever_snapshot:
        return [DOEFactor(**item) for item in campaign.lever_snapshot]
    return factors_for_requirement(req)

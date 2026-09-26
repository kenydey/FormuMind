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

    P2: high-frequency chemical exclusions (reactive metal × acid, carbonate ×
    acid, strong alkali × acid) are injected as ``DiscreteExcludeConstraint``
    across categorical ``mat_*`` slots so sampling budget is not spent on
    combinations the post-hoc gate would mark infeasible. Continuous-only
    numeric spaces still cannot express these and keep the post-hoc gate.
    """
    from baybe.constraints import ContinuousLinearConstraint
    from baybe.parameters import NumericalContinuousParameter
    from baybe.searchspace import SearchSpace

    from ....domain import knowledge

    parameters: list = []
    wt_names: list[str] = []
    mat_params: list[tuple[str, list[str]]] = []
    for slot_key, pool in candidate_pools.items():
        if len(pool) > 1:
            parameters.append(_material_parameter(slot_key, pool, knowledge))
            mat_params.append((f"mat_{slot_key}", list(pool)))
        low, high = _slot_bounds(genome, slot_key)
        wt_name = f"wt_{slot_key}"
        parameters.append(NumericalContinuousParameter(name=wt_name, bounds=(low, high)))
        wt_names.append(wt_name)

    if not parameters:
        raise ValueError("genome search space has no parameters")

    constraints: list = []
    if len(wt_names) >= 2:
        constraints.append(
            ContinuousLinearConstraint(
                parameters=wt_names,
                operator="<=",
                coefficients=tuple(1.0 for _ in wt_names),
                rhs=100.0,
            )
        )
    constraints.extend(discrete_exclude_constraints_for_pools(mat_params))
    return SearchSpace.from_product(parameters=parameters, constraints=constraints or None)


# High-frequency incompatible material-class pairs for DiscreteExclude.
# Drawn from acid_stability.toml hard rules (reactive metal / carbonate /
# strong alkali must not co-exist with acidic species in the same bath).
_ACID_NAME_HINTS = (
    "acid",
    "phosphoric",
    "sulfuric",
    "nitric",
    "hydrochloric",
    "acetic",
    "植酸",
    "磷酸",
    "硫酸",
    "硝酸",
    "盐酸",
)
_REACTIVE_METAL_HINTS = (
    "zinc dust",
    "zn dust",
    "aluminium powder",
    "aluminum powder",
    "镁粉",
    "锌粉",
)
_CARBONATE_HINTS = ("carbonate", "bicarbonate", "碳酸")
_ALKALI_HINTS = (
    "sodium hydroxide",
    "potassium hydroxide",
    "naoh",
    "koh",
    "氢氧化钠",
    "氢氧化钾",
)


def _name_matches(name: str, hints: tuple[str, ...]) -> bool:
    low = (name or "").lower()
    return any(h in low for h in hints)


def high_freq_exclude_pair_sets() -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """Return (left_hints, right_hints) used to classify incompatible materials."""
    return [
        (_REACTIVE_METAL_HINTS, _ACID_NAME_HINTS),
        (_CARBONATE_HINTS, _ACID_NAME_HINTS),
        (_ALKALI_HINTS, _ACID_NAME_HINTS),
    ]


def discrete_exclude_constraints_for_pools(
    mat_params: list[tuple[str, list[str]]],
) -> list:
    """Build BayBE DiscreteExcludeConstraint list for categorical material slots.

    Returns an empty list when baybe is unavailable, fewer than two mat params
    exist, or no pool members match a high-frequency exclusion pair. Fail-open:
    never raises into the recommend path.
    """
    if len(mat_params) < 2:
        return []
    try:
        from baybe.constraints import DiscreteExcludeConstraint, SubSelectionCondition
    except Exception:
        return []

    out: list = []
    pair_sets = high_freq_exclude_pair_sets()
    for i in range(len(mat_params)):
        for j in range(i + 1, len(mat_params)):
            p_a, pool_a = mat_params[i]
            p_b, pool_b = mat_params[j]
            for left_hints, right_hints in pair_sets:
                left_a = [m for m in pool_a if _name_matches(m, left_hints)]
                right_b = [m for m in pool_b if _name_matches(m, right_hints)]
                if left_a and right_b:
                    out.append(
                        DiscreteExcludeConstraint(
                            parameters=[p_a, p_b],
                            combiner="AND",
                            conditions=[
                                SubSelectionCondition(selection=left_a),
                                SubSelectionCondition(selection=right_b),
                            ],
                        )
                    )
                left_b = [m for m in pool_b if _name_matches(m, left_hints)]
                right_a = [m for m in pool_a if _name_matches(m, right_hints)]
                if left_b and right_a:
                    out.append(
                        DiscreteExcludeConstraint(
                            parameters=[p_b, p_a],
                            combiner="AND",
                            conditions=[
                                SubSelectionCondition(selection=left_b),
                                SubSelectionCondition(selection=right_a),
                            ],
                        )
                    )
    return out


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

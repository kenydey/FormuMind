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


def apply_requirement_bounds(req: Requirement, factors: list[DOEFactor]) -> list[DOEFactor]:
    """Tighten factor bounds from Requirement constraints before BayBE search."""
    constraints = normalize_constraints(req)
    adjusted: list[DOEFactor] = []
    for factor in factors:
        low, high = float(factor.low), float(factor.high)
        name_lower = factor.name.lower()

        if req.cure_temperature_c is not None and "cure" in name_lower and "temp" in name_lower:
            high = min(high, float(req.cure_temperature_c))
        if req.ph_target is not None and "ph" in name_lower:
            target = float(req.ph_target)
            low = max(low, target - 1.5)
            high = min(high, target + 1.5)

        voc_label = constraints.get("VOC 上限")
        if voc_label is not None and "voc" in name_lower:
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

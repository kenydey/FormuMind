"""Map FormuMind objectives to baybe targets."""
from __future__ import annotations

from ....domain.schemas import ObjectiveSpec, Requirement
from ....pipeline.workflow import OBJECTIVE, default_objectives


def _numerical_target(obj: ObjectiveSpec):
    from baybe.targets import NumericalTarget

    direction = (obj.direction or "maximize").lower()
    if direction == "match_target" and obj.target_value is not None:
        match_val = float(obj.target_value)
        sigma = max(1.0, abs(match_val) * 0.05)
        if hasattr(NumericalTarget, "match_bell"):
            return NumericalTarget.match_bell(obj.metric, match_value=match_val, sigma=sigma)
        return NumericalTarget(name=obj.metric, mode="MATCH", bounds=(match_val, match_val))

    if direction == "minimize":
        try:
            return NumericalTarget(name=obj.metric, minimize=True)
        except TypeError:
            return NumericalTarget(name=obj.metric, mode="MIN")

    try:
        return NumericalTarget(name=obj.metric, minimize=False)
    except TypeError:
        return NumericalTarget(name=obj.metric, mode="MAX")


def build_objective_from_specs(objectives: list[ObjectiveSpec]):
    """Build BayBE objective from an explicit objective spec list (Campaign snapshot SSOT)."""
    from baybe.objectives import ParetoObjective, SingleTargetObjective

    if not objectives:
        raise ValueError("Cannot build BayBE objective from empty objectives list")
    if len(objectives) > 1:
        targets = [_numerical_target(o) for o in objectives]
        return ParetoObjective(targets=targets)
    obj = objectives[0]
    if (obj.direction or "").lower() == "match_target":
        return SingleTargetObjective(target=_numerical_target(obj))
    return SingleTargetObjective(target=_numerical_target(obj))


def build_objective(req: Requirement):
    objectives = req.objectives or default_objectives(req.domain)
    return build_objective_from_specs(objectives)


def primary_metric(req: Requirement) -> str:
    objectives = req.objectives or default_objectives(req.domain)
    if objectives:
        return objectives[0].metric
    return OBJECTIVE[req.domain]

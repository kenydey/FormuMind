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


def build_objective(req: Requirement):
    from baybe.objectives import ParetoObjective, SingleTargetObjective

    objectives = req.objectives or default_objectives(req.domain)
    if len(objectives) > 1:
        targets = [_numerical_target(o) for o in objectives]
        return ParetoObjective(targets=targets)
    metric = primary_metric(req)
    obj = objectives[0] if objectives else None
    if obj and (obj.direction or "").lower() == "match_target":
        return SingleTargetObjective(target=_numerical_target(obj))
    from baybe.targets import NumericalTarget

    try:
        target = NumericalTarget(name=metric, minimize=False)
    except TypeError:
        target = NumericalTarget(name=metric, mode="MAX")
    return SingleTargetObjective(target=target)


def primary_metric(req: Requirement) -> str:
    objectives = req.objectives or default_objectives(req.domain)
    if objectives:
        return objectives[0].metric
    return OBJECTIVE[req.domain]

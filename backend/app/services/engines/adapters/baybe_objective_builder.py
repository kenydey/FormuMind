"""Map FormuMind objectives to baybe targets."""
from __future__ import annotations

from ....domain.schemas import Requirement
from ....pipeline.workflow import OBJECTIVE, default_objectives


def build_objective(req: Requirement):
    from baybe.objectives import ParetoObjective, SingleTargetObjective
    from baybe.targets import NumericalTarget

    objectives = req.objectives or default_objectives(req.domain)
    if len(objectives) > 1:
        targets = [
            NumericalTarget(name=o.metric, mode="MAX" if o.direction == "maximize" else "MIN")
            for o in objectives
        ]
        return ParetoObjective(targets=targets)
    metric = primary_metric(req)
    return SingleTargetObjective(target=NumericalTarget(name=metric))


def primary_metric(req: Requirement) -> str:
    objectives = req.objectives or default_objectives(req.domain)
    if objectives:
        return objectives[0].metric
    return OBJECTIVE[req.domain]

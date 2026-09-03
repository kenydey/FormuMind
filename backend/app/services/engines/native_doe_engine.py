"""Native numpy DOE engine — delegates to domain/doe.py."""
from __future__ import annotations

from ...domain import doe as doe_engine
from ...domain.schemas import DOEFactor, DOEPlan


def build_native_plan(
    factors: list[DOEFactor],
    design: str,
    n: int | None = None,
) -> DOEPlan:
    plan = doe_engine.build_plan(factors, design=design, n=n)
    if "engine=" not in plan.notes:
        plan.notes = f"engine=native; {plan.notes}"
    return plan

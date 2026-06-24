"""Resolve DOE engine selection (native / pydoe / auto)."""
from __future__ import annotations

from ...domain.schemas import DOEFactor, DOEPlan
from .native_doe_engine import build_native_plan
from .pydoe_engine import PYDOE_DESIGNS, build_plan_with_fallback, pydoe_available


def baybe_available() -> bool:
    try:
        import baybe  # noqa: F401

        return True
    except Exception:
        return False


def resolve_doe_engine(engine: str, design: str) -> str:
    """Return the concrete engine name that will be used."""
    eng = (engine or "auto").lower()
    if eng == "auto":
        if pydoe_available() and design in PYDOE_DESIGNS:
            return "pydoe"
        return "native"
    if eng == "pydoe":
        return "pydoe" if pydoe_available() else "native"
    return "native"


def build_doe_plan(
    factors: list[DOEFactor],
    design: str,
    *,
    engine: str = "auto",
    n: int | None = None,
) -> DOEPlan:
    resolved = resolve_doe_engine(engine, design)
    if resolved == "pydoe":
        return build_plan_with_fallback(factors, design, n=n)
    return build_native_plan(factors, design, n=n)

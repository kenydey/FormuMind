"""Resolve DOE engine selection (native / pydoe / auto)."""
from __future__ import annotations

import logging
from ..errors import log_handled_exception
from ...domain.schemas import DOEFactor, DOEPlan
from .native_doe_engine import build_native_plan
from .pydoe_engine import (
    _MIXTURE_DESIGNS,
    PYDOE_DESIGNS,
    build_plan_with_fallback,
    pydoe_available,
)

logger = logging.getLogger(__name__)


def baybe_available() -> bool:
    try:
        import baybe  # noqa: F401

        return True
    except Exception as exc:
        log_handled_exception(logger, exc, "optional feature check")
        return False


def resolve_doe_engine(engine: str, design: str) -> str:
    """Return the concrete engine name that will be used."""
    eng = (engine or "auto").lower()
    if eng == "auto":
        if pydoe_available() and design in PYDOE_DESIGNS:
            return "pydoe"
        # Mixture designs must keep explicit-failure semantics even when pyDOE
        # is missing — never let native raise a generic "Unknown design".
        if design in _MIXTURE_DESIGNS:
            return "pydoe"
        return "native"
    if eng == "pydoe":
        # Non-mixture designs still fall back to native when pyDOE is absent
        # (preserves historical resolve behaviour). Mixture designs always route
        # through the pydoe helper so failures raise ValueError("混料…") instead
        # of a generic native "Unknown design".
        if design in _MIXTURE_DESIGNS:
            return "pydoe"
        return "pydoe" if pydoe_available() else "native"
    return "native"


def _is_face_centred(ccd_alpha) -> bool:
    """True for the default CCD (None / "" / "face" / alpha == 1): the one pyDOE can build."""
    if ccd_alpha is None or (isinstance(ccd_alpha, str) and ccd_alpha.strip().lower() in ("", "face", "faced")):
        return True
    try:
        return float(ccd_alpha) == 1.0
    except (TypeError, ValueError):
        return False


def build_doe_plan(
    factors: list[DOEFactor],
    design: str,
    *,
    engine: str = "auto",
    n: int | None = None,
    requirement=None,
    seed: int | None = None,
    ccd_alpha: str | float | None = None,
) -> DOEPlan:
    if design == "ccd" and not _is_face_centred(ccd_alpha):
        # pyDOE cannot be asked for an arbitrary alpha, and the pyDOE adapter clips every star point into [low, high] -
        # which is face-centred by another name. A rotatable (or explicit-alpha) CCD keeps its star points outside the
        # box and flags them infeasible, so it is built by the native generator whichever engine was asked for.
        return build_native_plan(factors, design, n=n, ccd_alpha=ccd_alpha)
    resolved = resolve_doe_engine(engine, design)
    if resolved == "pydoe":
        return build_plan_with_fallback(
            factors, design, n=n, requirement=requirement, seed=seed
        )
    return build_native_plan(factors, design, n=n, ccd_alpha=ccd_alpha)

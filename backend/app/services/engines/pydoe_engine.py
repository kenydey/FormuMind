"""pyDOE-backed DOE engine for cold-start experimental designs."""
from __future__ import annotations

import logging
from ..errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal
import numpy as np

from ...domain.schemas import DOEFactor, DOEPlan
from .adapters.doe_adapter import matrix_to_doe_plan
from .native_doe_engine import build_native_plan

logger = logging.getLogger(__name__)

PYDOE_DESIGNS = frozenset({"lhs", "ccd", "bbdesign", "simplex_lattice", "sobol"})


def pydoe_available() -> bool:
    try:
        import pydoe  # noqa: F401

        return True
    except Exception as exc:
        log_handled_exception(logger, exc, "optional feature check")
        return False


def _default_n(k: int, n: int | None) -> int:
    return n or max(2 * k + 1, 8)


def _generate_matrix(design: str, k: int, n: int) -> np.ndarray:
    import pydoe

    if design == "lhs":
        raw = pydoe.lhs(k, n)
    elif design == "ccd":
        fn = getattr(pydoe, "ccdesign", None) or getattr(pydoe, "ccd", None)
        if fn is None:
            raise ValueError("pydoe has no central composite design function")
        raw = fn(k)
    elif design == "bbdesign":
        fn = getattr(pydoe, "bbdesign", None) or getattr(pydoe, "bb", None)
        if fn is None:
            raise ValueError("pydoe has no Box-Behnken design function")
        raw = fn(k)
    elif design == "simplex_lattice":
        fn = getattr(pydoe, "simplex_lattice_design", None)
        if fn is None:
            raise ValueError("pydoe has no simplex_lattice_design")
        # degree=2 → moderate number of mixture points for k components
        raw = fn(k, degree=2)
    elif design == "sobol":
        fn = getattr(pydoe, "sobol_sequence", None)
        if fn is None:
            raise ValueError("pydoe has no sobol_sequence")
        raw = fn(n, k)
    else:
        raise ValueError(f"Design {design!r} is not supported by pydoe engine")

    matrix = np.asarray(raw, dtype=float)
    if matrix.ndim == 1:
        matrix = matrix.reshape(-1, 1)
    return matrix


def build_pydoe_plan(
    factors: list[DOEFactor],
    design: str,
    n: int | None = None,
) -> DOEPlan:
    if not pydoe_available():
        raise RuntimeError("pydoe is not installed")

    k = len(factors)
    if k == 0:
        raise ValueError("At least one factor is required")

    # Designs with fixed run counts ignore n
    if design in ("ccd", "bbdesign", "simplex_lattice"):
        matrix = _generate_matrix(design, k, n or 0)
    else:
        matrix = _generate_matrix(design, k, _default_n(k, n))

    return matrix_to_doe_plan(matrix, factors, design, engine="pydoe")


def build_plan_with_fallback(
    factors: list[DOEFactor],
    design: str,
    n: int | None = None,
) -> DOEPlan:
    """Try pydoe; fall back to native for unknown designs or import failures."""
    if design not in PYDOE_DESIGNS:
        return build_native_plan(factors, design, n=n)
    try:
        return build_pydoe_plan(factors, design, n=n)
    except Exception:
        native_design = design if design in {"lhs", "ccd"} else "lhs"
        plan = build_native_plan(factors, native_design, n=n)
        plan.notes = f"engine=native (pydoe fallback); {plan.notes}"
        return plan

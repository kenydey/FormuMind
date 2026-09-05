"""pyDOE-backed DOE engine for cold-start experimental designs."""
from __future__ import annotations

import logging
from ..errors import log_handled_exception
import numpy as np

from ...domain.schemas import DOEFactor, DOEPlan
from .adapters.doe_adapter import matrix_to_doe_plan
from .native_doe_engine import build_native_plan

logger = logging.getLogger(__name__)

PYDOE_DESIGNS = frozenset({"lhs", "ccd", "bbdesign", "simplex_lattice", "sobol"})
# 2026-09-04 (P0): 混料设计的前提是"各成分和=100%", 无约束 LHS 兜底会
# 静默生成总量偏离 100% 的配方 —— 混料失败必须显式报错, 不允许降级。
_MIXTURE_DESIGNS = frozenset({"simplex_lattice", "simplex_centroid"})


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
    requirement: "Requirement | None" = None,
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

    plan = matrix_to_doe_plan(matrix, factors, design, engine="pydoe")

    # KG chemical-compatibility gate: if the baseline formulation skeleton
    # carries an INHIBITS relation, mark every run infeasible. Mirrors the
    # baybe_engine gate so both DOE engines produce consistent results.
    if requirement is not None:
        try:
            # engines/ → services/: one dot up, not ``..services`` (that resolves
            # to app.services.services and silently no-ops the whole gate).
            from ..kg_chemical_check import check_formulation_chemistry
            from ...domain import knowledge

            skeleton = (
                requirement.active_formulation
                or knowledge.baseline_formulation(requirement)
            )
            if skeleton is not None:
                chk = check_formulation_chemistry(skeleton, include_synergies=False)
                if not chk.feasible:
                    for run in plan.runs:
                        run.infeasible = True
                        run.infeasible_reason = (
                            "; ".join(chk.reasons)
                            or "知识图谱检测到材料不相容"
                        )
        except Exception as exc:
            # Gate must never break DOE generation, but swallow-without-log
            # hid the broken import for weeks — keep a debug breadcrumb.
            logger.debug("KG chemical gate skipped (%s); allowing", exc)

    return plan


def build_plan_with_fallback(
    factors: list[DOEFactor],
    design: str,
    n: int | None = None,
    requirement: "Requirement | None" = None,
) -> DOEPlan:
    """Try pydoe; fall back to native for unknown designs or import failures.

    Mixture designs (simplex_*) never fall back to LHS: the mixture premise
    (component sum = 100%) does not survive an unconstrained LHS, so a pyDOE
    failure on a mixture design raises instead of degrading silently.
    """
    if design not in PYDOE_DESIGNS:
        # Unknown mixture names must not collapse to native "Unknown design" —
        # surface the mixture constraint failure explicitly.
        if design in _MIXTURE_DESIGNS:
            raise ValueError(
                f"混料设计 {design!r} 不受支持或 pyDOE 不可用 — "
                "混料约束(成分和=100%)无法由无约束 LHS 兜底"
            )
        return build_native_plan(factors, design, n=n)
    try:
        return build_pydoe_plan(factors, design, n=n, requirement=requirement)
    except Exception as exc:
        if design in _MIXTURE_DESIGNS:
            raise ValueError(
                f"混料设计 {design!r} 生成失败: {exc} — "
                "混料约束(成分和=100%)无法由无约束 LHS 兜底, 请检查因子数/设计参数"
            ) from exc
        native_design = design if design in {"lhs", "ccd"} else "lhs"
        plan = build_native_plan(factors, native_design, n=n)
        plan.notes = f"engine=native (pydoe fallback: {exc}); {plan.notes}"
        return plan

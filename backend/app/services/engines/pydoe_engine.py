"""pyDOE-backed DOE engine for cold-start experimental designs."""
from __future__ import annotations

import logging
from ..errors import log_handled_exception
import numpy as np

from ...domain.schemas import DOEFactor, DOEPlan, Requirement
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


def _generate_matrix(
    design: str, k: int, n: int, seed: int | None = None
) -> np.ndarray:
    import pydoe

    if design == "lhs":
        # pydoe>=1.0 uses OS entropy when no seed is given -> non-reproducible
        # plans. Thread an explicit seed; `seed=` is the non-deprecated
        # parameter in pydoe>=1.0 (random_state= is deprecated in 1.5).
        # v18-14: seed=None 时映射为 0，与 native 引擎一致（"不指定即确定性默认"），
        # 消除环境依赖行为（是否装 pydoe 导致同一请求行为不同）。
        raw = pydoe.lhs(k, n, seed=seed if seed is not None else 0)
    elif design == "ccd":
        fn = getattr(pydoe, "ccdesign", None) or getattr(pydoe, "ccd", None)
        if fn is None:
            raise ValueError("pydoe has no central composite design function")
        # v27 P2-22: 与 native 对齐 —— face-centred + 3 个中心点（2 cube + 1 axial）。
        # pydoe 默认 center=(4,4) 给 8 个中心点，k=2 时 16 runs vs native 11 runs。
        raw = fn(k, face="faced", center=(2, 1))
    elif design == "bbdesign":
        fn = getattr(pydoe, "bbdesign", None) or getattr(pydoe, "bb", None)
        if fn is None:
            raise ValueError("pydoe has no Box-Behnken design function")
        raw = fn(k)
    elif design == "simplex_lattice":
        fn = getattr(pydoe, "simplex_lattice_design", None)
        if fn is None:
            raise ValueError("pydoe has no simplex_lattice_design")
        # degree m=2 → moderate number of mixture points for k components.
        # pydoe>=1.0 signature is positional (q, m); older pydoe2 accepted
        # the same positionally, so avoid the `degree=` keyword.
        raw = fn(k, 2)
    elif design == "sobol":
        fn = getattr(pydoe, "sobol_sequence", None)
        if fn is None:
            raise ValueError("pydoe has no sobol_sequence")
        # v27 P0-4: pydoe>=1.0 默认 use_pow_of_2=True 会把 n 静默放大到 2 的幂
        #（要 10 得 16）；显式关闭，旧版无此参数时回退旧行为。
        try:
            raw = fn(n, k, use_pow_of_2=False)
        except TypeError:
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
    seed: int | None = None,
) -> DOEPlan:
    if not pydoe_available():
        raise RuntimeError("pydoe is not installed")

    # C-4a: pydoe designs sample a continuous space (LHS etc.) — a discrete
    # factor level set cannot be sampled, so fail closed with an explicit
    # error instead of silently degrading to a continuous approximation.
    discrete = [f.name for f in factors if f.kind == "discrete"]
    if discrete:
        raise ValueError(
            f"pydoe engine does not support discrete factors {discrete}; "
            "use engine='native' or engine='baybe'"
        )

    k = len(factors)
    if k == 0:
        raise ValueError("At least one factor is required")

    if design in _MIXTURE_DESIGNS:
        # The simplex spans the recipe components only; temperatures and the like are not shares of a whole.
        from .adapters.doe_adapter import split_mixture_factors

        k = len(split_mixture_factors(factors)[0])
        if k < 2:
            raise ValueError(
                f"混料设计 {design!r} 需要至少 2 个以 wt% 计的配方成分因子，当前只有 {k} 个 —— "
                "请换用 LHS / 因子设计，或把成分因子的单位设为 wt%"
            )

    # Designs with fixed run counts ignore n
    if design in ("ccd", "bbdesign", "simplex_lattice"):
        matrix = _generate_matrix(design, k, n or 0, seed=seed)
    else:
        matrix = _generate_matrix(design, k, _default_n(k, n), seed=seed)

    plan = matrix_to_doe_plan(matrix, factors, design, engine="pydoe")

    # v13-5: KG 门已由 doe_registry.apply_kg_chemical_gate 统一执行，此处内联
    # 门删除（曾致双执行）。
    return plan


def build_plan_with_fallback(
    factors: list[DOEFactor],
    design: str,
    n: int | None = None,
    requirement: "Requirement | None" = None,
    seed: int | None = None,
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
        return build_native_plan(factors, design, n=n, seed=seed)
    try:
        return build_pydoe_plan(factors, design, n=n, requirement=requirement, seed=seed)
    except Exception as exc:
        if design in _MIXTURE_DESIGNS:
            raise ValueError(
                f"混料设计 {design!r} 生成失败: {exc} — "
                "混料约束(成分和=100%)无法由无约束 LHS 兜底, 请检查因子数/设计参数"
            ) from exc
        # v29 M-14: bbdesign 参数非法不再静默降级为 lhs ——
        # 用户要的是 Box-Behnken，给 lhs 是错的。直接报错让调用方修正参数。
        if design == "bbdesign":
            raise ValueError(
                f"bbdesign 生成失败: {exc} — "
                "请检查因子数(3-5为宜)/中心点参数，不自动降级为 lhs"
            ) from exc
        # v9: 离散因子的 fail-closed 不得被 fallback 击穿 ——
        # build_pydoe_plan 对离散 raise ValueError 是有意的设计，
        # 吞掉它会降级到 native lhs 的静默 clamp 路径（doe.py P1-7）。
        if isinstance(exc, ValueError) and "discrete factors" in str(exc):
            raise
        native_design = design if design in {"lhs", "ccd"} else "lhs"
        # v15: fallback 也透传 seed，保证"指定 seed → 可复现"承诺不断裂
        plan = build_native_plan(factors, native_design, n=n, seed=seed)
        plan.notes = f"engine=native (pydoe fallback: {exc}); {plan.notes}"
        return plan

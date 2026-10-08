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


def apply_kg_chemical_gate(plan: DOEPlan, requirement) -> DOEPlan:
    """P1-5: KG chemical-compatibility gate, engine-agnostic.

    If the baseline formulation skeleton carries an INHIBITS relation, mark
    every run infeasible. Applied in build_doe_plan regardless of engine
    (pydoe/native), so the safety gate no longer depends on pydoe being
    installed.
    """
    if requirement is None:
        return plan
    try:
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
        # Gate must never break DOE generation.
        # v13-5: fail-open 不得静默——warning + plan 留痕，调用方可见安全检查没跑。
        logger = logging.getLogger(__name__)
        logger.warning("KG chemical gate skipped (%s); allowing", exc)
        note = f"KG chemical gate skipped: {exc}"
        plan.notes = f"{plan.notes}\n{note}".strip() if plan.notes else note
    return plan


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
    # v27 P1-11: 固定 run 数设计的 n-ignored 提示统一收口到此。
    # v23 只在 domain/doe.py 的 else 分支加了提示，native ccd / full_factorial
    # 分支和 pydoe 路径漏网。notes 已带提示的不重复加。
    _FIXED_N = frozenset(
        {
            "full_factorial",
            "fractional_factorial",
            "plackett_burman",
            "ccd",
            "simplex_lattice",
        }
    )

    def _with_n_hint(plan: DOEPlan) -> DOEPlan:
        if n is not None and design in _FIXED_N and "ignored" not in (plan.notes or ""):
            hint = f"(n={n} ignored: {design} has fixed run count)"
            plan.notes = f"{plan.notes}\n{hint}".strip() if plan.notes else hint
        return plan

    if design == "ccd" and not _is_face_centred(ccd_alpha):
        # pyDOE cannot be asked for an arbitrary alpha, and the pyDOE adapter clips every star point into [low, high] -
        # which is face-centred by another name. A rotatable (or explicit-alpha) CCD keeps its star points outside the
        # box and flags them infeasible, so it is built by the native generator whichever engine was asked for.
        plan = build_native_plan(factors, design, n=n, ccd_alpha=ccd_alpha, seed=seed)
        return _with_n_hint(apply_kg_chemical_gate(plan, requirement))
    resolved = resolve_doe_engine(engine, design)
    if resolved == "pydoe":
        plan = build_plan_with_fallback(
            factors, design, n=n, requirement=requirement, seed=seed
        )
    else:
        plan = build_native_plan(factors, design, n=n, ccd_alpha=ccd_alpha, seed=seed)
    # P1-5: gate runs for every engine, not just pydoe.
    return _with_n_hint(apply_kg_chemical_gate(plan, requirement))

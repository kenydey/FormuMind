"""Convert design matrices / DataFrames into DOEPlan JSON contracts."""
from __future__ import annotations

import numpy as np

from ....domain.doe import decode
from ....domain.schemas import DOEFactor, DOEPlan, DOERun


#  pyDOE designs whose raw output is already unit-interval-scaled (each cell in
#  [0, 1]): lhs/sobol sample the unit hypercube directly. Mixture designs
#  (simplex_*) are proportions that sum to 1 and need a dedicated mapping —
#  independent per-factor decode() would destroy the sum=100% premise.
_UNIT_SCALE_DESIGNS = frozenset({"lhs", "sobol"})
_MIXTURE_DESIGNS = frozenset({"simplex_lattice", "simplex_centroid"})

#  A factor is a mixture *component* when it is a recipe share (wt%, or unit-less as in older plans).
#  Temperatures, times, g/L bath concentrations … are process settings: they do not add up to anything,
#  and putting them into the simplex is what produced "156 wt% epoxy at 0 °C" runs.
_COMPONENT_UNITS = frozenset({"", "wt%", "wt.%", "%", "mass%", "质量%", "重量%"})


def split_mixture_factors(factors: list[DOEFactor]) -> tuple[list[int], list[int]]:
    """Indices of (mixture components, process factors) within *factors*."""
    components: list[int] = []
    process: list[int] = []
    for i, factor in enumerate(factors):
        unit = (factor.unit or "").strip().lower().replace(" ", "")
        if factor.kind != "discrete" and unit in _COMPONENT_UNITS:
            components.append(i)
        else:
            process.append(i)
    return components, process


def _row_to_unit_interval(value: float, *, already_unit: bool) -> float:
    """Map a pyDOE raw value to [0, 1], given whether its design is unit-scaled.

    A magnitude-only check can't disambiguate the two scales — a raw value of,
    say, 0.5 is valid on both — so the caller must say which one `design` uses
    rather than have this guess from the value's range.
    """
    v = float(value)
    if already_unit:
        return float(np.clip(v, 0.0, 1.0))
    return float(np.clip((v + 1.0) / 2.0, 0.0, 1.0))


def unit_to_coded(unit: float) -> float:
    """Map [0, 1] → coded [-1, 1] for compatibility with native decode()."""
    return round(unit * 2.0 - 1.0, 4)


def _fit_to_bounds(y: np.ndarray, low: np.ndarray, high: np.ndarray, total: float) -> np.ndarray:
    """Project *y* onto {x : Σx = total, low ≤ x ≤ high} by shifting then clipping.

    ``Σ clip(y + λ, low, high)`` rises monotonically from Σlow to Σhigh, so the shift that lands
    exactly on *total* is found by bisection. Points already inside the box come back unchanged (λ = 0).
    """
    x = np.clip(y, low, high)
    if abs(float(x.sum()) - total) < 1e-9:
        return x
    lo, hi = float((low - y).min()), float((high - y).max())
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if float(np.clip(y + mid, low, high).sum()) < total:
            lo = mid
        else:
            hi = mid
    return np.clip(y + (lo + hi) / 2.0, low, high)


def _mixture_row_to_run(
    row: np.ndarray,
    factors: list[DOEFactor],
    *,
    run_id: int,
) -> DOERun:
    """Map a simplex proportion row (sum≈1) onto the recipe components of *factors*.

    The components share ``total = Σ midpoint`` — the mass the baseline recipe gives them — and each
    one starts at its lower bound (``low_i + p_i · (total − Σ low)``, the pseudo-component transform),
    then is pulled back inside ``[low, high]`` with the sum preserved. Process factors (temperature …)
    are not part of the simplex and stay at their midpoint. The old mapping was ``p · Σ high`` over
    *every* factor: 156 wt% of one resin, 0 °C cure — every run outside every declared bound.
    """
    components, process = split_mixture_factors(factors)
    props = np.asarray(row, dtype=float).reshape(-1)
    if props.size != len(components):
        raise ValueError(
            f"mixture row has {props.size} proportions but the plan has {len(components)} recipe components"
        )
    prop_sum = float(props.sum())
    props = props / prop_sum if prop_sum > 0 else np.full(len(components), 1.0 / max(len(components), 1))
    low = np.array([float(factors[i].low) for i in components])
    high = np.array([float(factors[i].high) for i in components])
    total = float(((low + high) / 2.0).sum())
    x = _fit_to_bounds(low + props * (total - float(low.sum())), low, high, total)

    natural: dict[str, float] = {}
    for idx, value in zip(components, x):
        natural[factors[idx].name] = round(float(value), 4)
    for idx in process:
        natural[factors[idx].name] = round((float(factors[idx].low) + float(factors[idx].high)) / 2.0, 4)

    coded: dict[str, float] = {}
    for factor in factors:
        half = (float(factor.high) - float(factor.low)) / 2.0
        mid = (float(factor.high) + float(factor.low)) / 2.0
        coded[factor.name] = unit_to_coded(float(np.clip(((natural[factor.name] - mid) / half + 1.0) / 2.0, 0.0, 1.0))) if half > 0 else 0.0
    return DOERun(run_id=run_id, coded=coded, natural={f.name: natural[f.name] for f in factors})


def matrix_to_doe_plan(
    matrix: np.ndarray,
    factors: list[DOEFactor],
    design: str,
    *,
    engine: str,
    extra_notes: str = "",
) -> DOEPlan:
    """Build a DOEPlan from a 2-D design matrix (rows = runs, cols = factors)."""
    if matrix.ndim != 2:
        raise ValueError("Design matrix must be 2-dimensional")
    # A mixture matrix has one column per recipe component, not per factor.
    expected = len(split_mixture_factors(factors)[0]) if design in _MIXTURE_DESIGNS else len(factors)
    if matrix.shape[1] != expected:
        raise ValueError(
            f"Matrix has {matrix.shape[1]} columns but {expected} "
            f"{'mixture components' if design in _MIXTURE_DESIGNS else 'factors'} were supplied"
        )

    runs: list[DOERun] = []
    if design in _MIXTURE_DESIGNS:
        for idx, row in enumerate(matrix, start=1):
            runs.append(_mixture_row_to_run(row, factors, run_id=idx))
        held = [factors[i].name for i in split_mixture_factors(factors)[1]]
        if held:
            held_note = f"process factors held at their midpoint: {', '.join(held)}."
            extra_notes = f"{extra_notes} {held_note}".strip()
    else:
        already_unit = design in _UNIT_SCALE_DESIGNS
        for idx, row in enumerate(matrix, start=1):
            coded: dict[str, float] = {}
            natural: dict[str, float] = {}
            for factor, raw in zip(factors, row):
                unit = _row_to_unit_interval(float(raw), already_unit=already_unit)
                c = unit_to_coded(unit)
                coded[factor.name] = c
                natural[factor.name] = decode(c, factor)
            runs.append(DOERun(run_id=idx, coded=coded, natural=natural))

    note = f"engine={engine}; {design} design over {len(factors)} factors → {len(runs)} runs."
    if extra_notes:
        note = f"{note} {extra_notes}"
    return DOEPlan(design=design, factors=factors, runs=runs, notes=note)


def dataframe_to_doe_plan(
    df,
    factors: list[DOEFactor],
    design: str,
    *,
    engine: str,
    ai_suggested: bool = True,
) -> DOEPlan:
    """Map a baybe recommend() DataFrame to DOEPlan.

    v29 M-16: 缺列警告 —— 此前因子名不在 DataFrame 时静默跳过，
    导致 DOE 计划缺因子。现 warning 日志。
    """
    import logging

    logger = logging.getLogger(__name__)
    # 预检查缺列
    df_cols = set(df.columns) if hasattr(df, "columns") else set()
    missing = [f.name for f in factors if f.name not in df_cols]
    if missing:
        logger.warning(
            "doe_adapter: DataFrame 缺 %d 列 %s（factors=%d），对应因子将跳过",
            len(missing), missing, len(factors),
        )
    rows = []
    for idx, (_, row) in enumerate(df.iterrows(), start=1):
        # B-DOE-3: 离散因子（C-4a）的 natural 值可能是字符串水平，
        # 不能无条件 float()。coded 按水平索引归一化。
        natural: dict = {}
        coded: dict = {}
        for f in factors:
            if f.name not in row:
                continue
            raw = row[f.name]
            if f.kind == "discrete":
                natural[f.name] = raw
                levels = list(f.levels or [])
                if raw in levels and len(levels) > 1:
                    unit = levels.index(raw) / (len(levels) - 1)
                else:
                    unit = 0.5
            else:
                natural[f.name] = round(float(raw), 4)
                unit = (
                    (natural[f.name] - f.low) / (f.high - f.low)
                    if f.high > f.low
                    else 0.5
                )
            coded[f.name] = unit_to_coded(float(np.clip(unit, 0.0, 1.0)))
        rows.append(
            DOERun(
                run_id=idx,
                coded=coded,
                natural=natural,
                ai_suggested=ai_suggested,
            )
        )
    note = f"engine={engine}; active-learning batch ({len(rows)} runs)."
    return DOEPlan(design=design, factors=factors, runs=rows, notes=note)

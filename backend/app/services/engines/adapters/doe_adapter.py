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


def _mixture_row_to_run(
    row: np.ndarray,
    factors: list[DOEFactor],
    *,
    run_id: int,
) -> DOERun:
    """Map a simplex proportion row (sum≈1) onto natural lever units.

    ``total = sum(factor.high)`` is the recipe mass/budget; each cell is a
    share of that total so ``sum(natural) ≈ total``.
    """
    props = np.asarray(row, dtype=float).reshape(-1)
    prop_sum = float(props.sum())
    if prop_sum <= 0:
        props = np.full(len(factors), 1.0 / max(len(factors), 1))
        prop_sum = 1.0
    else:
        props = props / prop_sum
    total = float(sum(f.high for f in factors))
    coded: dict[str, float] = {}
    natural: dict[str, float] = {}
    for factor, p in zip(factors, props):
        nat = round(float(p) * total, 4)
        natural[factor.name] = nat
        span = float(factor.high) - float(factor.low)
        unit = (nat - float(factor.low)) / span if span > 0 else 0.5
        coded[factor.name] = unit_to_coded(float(np.clip(unit, 0.0, 1.0)))
    return DOERun(run_id=run_id, coded=coded, natural=natural)


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
    if matrix.shape[1] != len(factors):
        raise ValueError(
            f"Matrix has {matrix.shape[1]} columns but {len(factors)} factors were supplied"
        )

    runs: list[DOERun] = []
    if design in _MIXTURE_DESIGNS:
        for idx, row in enumerate(matrix, start=1):
            runs.append(_mixture_row_to_run(row, factors, run_id=idx))
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
    """Map a baybe recommend() DataFrame to DOEPlan."""
    rows = []
    factor_names = [f.name for f in factors]
    for idx, (_, row) in enumerate(df.iterrows(), start=1):
        natural = {name: round(float(row[name]), 4) for name in factor_names if name in row}
        coded = {}
        for f in factors:
            if f.name in natural:
                unit = (natural[f.name] - f.low) / (f.high - f.low) if f.high > f.low else 0.5
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

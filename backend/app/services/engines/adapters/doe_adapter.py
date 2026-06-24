"""Convert design matrices / DataFrames into DOEPlan JSON contracts."""
from __future__ import annotations

import numpy as np

from ....domain.doe import decode
from ....domain.schemas import DOEFactor, DOEPlan, DOERun


def _row_to_unit_interval(value: float) -> float:
    """Map assorted pyDOE scales to [0, 1]."""
    v = float(value)
    if -1.05 <= v <= 1.05:
        return (v + 1.0) / 2.0
    if 0.0 <= v <= 1.0:
        return v
    return float(np.clip(v, 0.0, 1.0))


def unit_to_coded(unit: float) -> float:
    """Map [0, 1] → coded [-1, 1] for compatibility with native decode()."""
    return round(unit * 2.0 - 1.0, 4)


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
    for idx, row in enumerate(matrix, start=1):
        coded: dict[str, float] = {}
        natural: dict[str, float] = {}
        for factor, raw in zip(factors, row):
            unit = _row_to_unit_interval(float(raw))
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

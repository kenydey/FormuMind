"""ExperimentRecord ↔ pandas for baybe Campaign.add_measurements."""
from __future__ import annotations

from ....domain.schemas import ExperimentRecord, Requirement
from .baybe_objective_builder import primary_metric


def records_to_dataframe(records: list[ExperimentRecord], req: Requirement):
    import pandas as pd

    if not records:
        return pd.DataFrame()

    metric = primary_metric(req)
    rows = []
    for rec in records:
        row = dict(rec.factors)
        if rec.cure_temperature_c is not None and "cure_temperature_c" not in row:
            row["cure_temperature_c"] = rec.cure_temperature_c
        value = rec.measured.get(metric)
        if value is None and rec.measured:
            value = next(iter(rec.measured.values()))
        row[metric] = value
        rows.append(row)
    return pd.DataFrame(rows)


def surrogate_measurements_from_plan(plan, req: Requirement, objective_metric: str):
    """Build virtual measurements from predictor for cold-start baybe init."""
    import pandas as pd

    from ....pipeline import reconstruct
    from ....services import predictor

    rows = []
    for run in plan.runs:
        try:
            form = reconstruct.formulation_from_factors(req.domain, run.natural)
            props = predictor.predict(form)
            val = props.get(objective_metric, 0.0)
        except Exception:
            val = 0.0
        row = dict(run.natural)
        row[objective_metric] = val
        rows.append(row)
    return pd.DataFrame(rows)

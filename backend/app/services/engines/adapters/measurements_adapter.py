"""ExperimentRecord ↔ pandas for baybe Campaign.add_measurements."""
from __future__ import annotations

from ...errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal
from ....domain.objective_contract import objective_metrics, normalize_objectives
from ....domain.schemas import ExperimentRecord, Requirement
from .baybe_objective_builder import primary_metric


def _metrics_for_req(req: Requirement) -> list[str]:
    objectives = normalize_objectives(req)
    metrics = objective_metrics(objectives)
    return metrics or [primary_metric(req)]


def records_to_dataframe(
    records: list[ExperimentRecord],
    req: Requirement,
    objectives: list | None = None,
):
    import pandas as pd

    from ....domain.objective_contract import normalize_objectives, objective_metrics

    if not records:
        return pd.DataFrame()

    if objectives is None:
        objectives = normalize_objectives(req)
    metrics = objective_metrics(objectives) or [primary_metric(req)]
    rows = []
    for rec in records:
        row = dict(rec.factors)
        if rec.cure_temperature_c is not None and "cure_temperature_c" not in row:
            row["cure_temperature_c"] = rec.cure_temperature_c
        for metric in metrics:
            value = rec.measured.get(metric)
            if value is None and metric == metrics[0] and rec.measured:
                value = next(iter(rec.measured.values()), None)
            row[metric] = value
        rows.append(row)
    return pd.DataFrame(rows)


def surrogate_measurements_from_plan(plan, req: Requirement, objective_metric: str | None = None):
    """Build virtual measurements from predictor for cold-start baybe init."""
    import pandas as pd

    from ....pipeline import reconstruct
    from ....services import predictor

    metrics = _metrics_for_req(req)
    if objective_metric and objective_metric not in metrics:
        metrics = [objective_metric, *metrics]

    rows = []
    for run in plan.runs:
        row = dict(run.natural)
        try:
            form = reconstruct.formulation_from_factors(req, run.natural)
            props = predictor.predict(form)
            for metric in metrics:
                row[metric] = props.get(metric, 0.0)
        except Exception:
            for metric in metrics:
                row[metric] = 0.0
        rows.append(row)
    return pd.DataFrame(rows)

"""Out-of-spec results as negative evidence for the next experiment batch.

A failed run is expensive information and it was being half-used. Failures do
reach the surrogate as ordinary training rows, but *the fact that they failed*
went nowhere: nothing read ``Measurement.passed``, so active learning kept
proposing points next to a composition already known not to work — the
expected-improvement score is blind to a neighbour that blew its spec, because
a low measured value and a spec breach look identical to it.

This turns those results into a repulsion term over the factor space. It is
deliberately a *penalty on selection*, not a hard exclusion: a near-miss on one
spec may still be the most informative next run, and the search should be
allowed to take that bet — just not for free.
"""
from __future__ import annotations

import math

from loguru import logger

from ..domain.schemas import ExperimentRecord, ProductDomain

# How strongly a coincident failure suppresses a candidate. 1.0 zeroes the
# acquisition value at zero distance and decays with the squared-exponential
# kernel below.
_PENALTY_WEIGHT = 1.0
# Distance (in normalised factor space) at which a failure stops mattering.
_KERNEL_LENGTH = 0.35


def failed_records(
    domain: ProductDomain,
    project_id: str = "",
) -> list[ExperimentRecord]:
    """Experiments with at least one measurement outside its spec window.

    Reads the typed measurement rows, so this only returns something once
    results carry acceptance limits — a bare number has no verdict to read.
    """
    from ..db.measurement_store import get_measurement_store
    from .training import registry

    try:
        store = get_measurement_store()
        failing = store.failing()
    except Exception as exc:
        logger.debug("failure memory: measurement store unavailable (%s)", exc)
        return []
    if not failing:
        return []

    failed_ids = {row.experiment_id for row in failing}
    if not failed_ids:
        return []

    # Map experiment rows back to records by label, the only stable join between
    # the typed rows and the in-memory registry.
    labels = _labels_for(failed_ids)
    out = [
        rec
        for rec in registry.records_for(domain, project_id)
        if rec.label and rec.label in labels
    ]
    return out


def _labels_for(experiment_ids: set[int]) -> set[str]:
    from ..db.database import default_session_factory
    from ..db.models import ExperimentRow

    try:
        with default_session_factory()() as session:
            rows = (
                session.query(ExperimentRow.label)
                .filter(ExperimentRow.id.in_(experiment_ids))
                .all()
            )
        return {r[0] for r in rows if r[0]}
    except Exception as exc:
        logger.debug("failure memory: label lookup failed (%s)", exc)
        return set()


def _normalised_distance(
    a: dict[str, float],
    b: dict[str, float],
    spans: dict[str, float],
) -> float:
    """Euclidean distance over shared factors, each scaled by its own span.

    Factors live on wildly different scales — a 2 wt% inhibitor change and a
    20 °C cure change are not comparable raw — so an unnormalised distance
    would be dominated by whichever factor happens to have the largest units.
    """
    shared = [k for k in a if k in b and spans.get(k)]
    if not shared:
        return float("inf")
    total = 0.0
    for key in shared:
        span = spans[key] or 1.0
        total += ((float(a[key]) - float(b[key])) / span) ** 2
    return math.sqrt(total / len(shared))


def factor_spans(records: list[ExperimentRecord], candidate: dict[str, float]) -> dict[str, float]:
    """Observed range per factor, used to normalise distances."""
    spans: dict[str, float] = {}
    keys = set(candidate) | {k for rec in records for k in rec.factors}
    for key in keys:
        values = [float(rec.factors[key]) for rec in records if key in rec.factors]
        if key in candidate:
            values.append(float(candidate[key]))
        if len(values) >= 2:
            spread = max(values) - min(values)
            spans[key] = spread if spread > 1e-9 else 1.0
        elif values:
            spans[key] = abs(values[0]) or 1.0
    return spans


def penalty_for(
    candidate: dict[str, float],
    failures: list[ExperimentRecord],
    spans: dict[str, float] | None = None,
) -> float:
    """Suppression factor in [0, 1] — 1.0 means "no known failure nearby".

    Squared-exponential falloff, so a candidate sitting on top of a failed run
    is heavily suppressed and one a full span away is untouched.
    """
    if not failures:
        return 1.0
    spans = spans if spans is not None else factor_spans(failures, candidate)
    worst = 0.0
    for failure in failures:
        distance = _normalised_distance(candidate, failure.factors, spans)
        if not math.isfinite(distance):
            continue
        worst = max(worst, math.exp(-((distance / _KERNEL_LENGTH) ** 2)))
    return max(0.0, 1.0 - _PENALTY_WEIGHT * worst)

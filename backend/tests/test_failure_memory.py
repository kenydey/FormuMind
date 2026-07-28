"""Out-of-spec results steering the next experiment batch.

Expected improvement cannot distinguish a merely low value from a breached
acceptance limit, so without this the loop keeps proposing neighbours of a
composition already known not to work.
"""
from __future__ import annotations

import pytest

from app.domain.schemas import DOEPlan, DOERun, ExperimentRecord, Measurement, ProductDomain
from app.services.active_learning import suggest_next_experiments
from app.services.failure_memory import factor_spans, penalty_for

_SPANS = {"Zinc phosphate": 12.0, "Polyamide hardener": 14.0}


def _failure(zinc: float = 4.0, hardener: float = 14.0) -> ExperimentRecord:
    return ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating,
        factors={"Zinc phosphate": zinc, "Polyamide hardener": hardener},
        measured={"salt_spray_hours": 300.0},
        label="failed-run",
    )


# ── the penalty ──────────────────────────────────────────────────────────────


def test_penalty_is_strongest_at_the_failure_and_decays_with_distance():
    failures = [_failure()]
    at_failure = penalty_for({"Zinc phosphate": 4.0, "Polyamide hardener": 14.0}, failures, _SPANS)
    near = penalty_for({"Zinc phosphate": 4.5, "Polyamide hardener": 14.5}, failures, _SPANS)
    mid = penalty_for({"Zinc phosphate": 8.0, "Polyamide hardener": 18.0}, failures, _SPANS)
    far = penalty_for({"Zinc phosphate": 14.0, "Polyamide hardener": 22.0}, failures, _SPANS)

    assert at_failure == pytest.approx(0.0, abs=1e-6)
    assert at_failure < near < mid < far
    assert far > 0.9


def test_no_failures_means_no_suppression():
    assert penalty_for({"Zinc phosphate": 4.0}, []) == 1.0


def test_candidate_sharing_no_factors_is_untouched():
    """An infinite distance must not be read as 'right on top of a failure'."""
    assert penalty_for({"Unrelated": 1.0}, [_failure()], {"Unrelated": 1.0}) == 1.0


def test_penalty_stays_within_bounds():
    failures = [_failure(4.0), _failure(5.0), _failure(6.0)]
    for zinc in range(0, 20):
        value = penalty_for({"Zinc phosphate": float(zinc)}, failures, _SPANS)
        assert 0.0 <= value <= 1.0


def test_nearest_failure_dominates():
    """Several distant failures must not add up to suppress a good candidate."""
    far = [_failure(20.0), _failure(22.0), _failure(24.0)]
    assert penalty_for({"Zinc phosphate": 4.0, "Polyamide hardener": 14.0}, far, _SPANS) > 0.9


def test_factor_spans_normalise_across_scales():
    """A 2 wt% move and a 20 °C move are not comparable raw."""
    records = [
        ExperimentRecord(
            domain=ProductDomain.anticorrosion_coating,
            factors={"Zinc phosphate": 2.0, "cure_temperature_c": 60.0},
            measured={"salt_spray_hours": 1.0},
        ),
        ExperimentRecord(
            domain=ProductDomain.anticorrosion_coating,
            factors={"Zinc phosphate": 14.0, "cure_temperature_c": 120.0},
            measured={"salt_spray_hours": 1.0},
        ),
    ]
    spans = factor_spans(records, {})
    assert spans["Zinc phosphate"] == pytest.approx(12.0)
    assert spans["cure_temperature_c"] == pytest.approx(60.0)


def test_span_never_zero_for_a_constant_factor():
    """A zero span would divide by zero in the distance calculation."""
    records = [
        ExperimentRecord(
            domain=ProductDomain.anticorrosion_coating,
            factors={"fixed": 5.0}, measured={"m": 1.0},
        ),
        ExperimentRecord(
            domain=ProductDomain.anticorrosion_coating,
            factors={"fixed": 5.0}, measured={"m": 1.0},
        ),
    ]
    assert factor_spans(records, {})["fixed"] > 0


# ── it changes what gets selected ────────────────────────────────────────────


def _plan() -> DOEPlan:
    runs = [
        DOERun(run_id=i, coded={}, natural={"Zinc phosphate": float(z)})
        for i, z in enumerate([4.0, 8.0, 12.0], start=1)
    ]
    return DOEPlan(design="lhs", factors=[], runs=runs,
                   domain=ProductDomain.anticorrosion_coating)


def test_selection_avoids_a_known_failure(monkeypatch):
    import app.services.active_learning as al

    # Flat acquisition, so ordering is decided purely by the failure penalty.
    monkeypatch.setattr(al, "_surrogate_score", lambda *a, **k: (100.0, 10.0))
    monkeypatch.setattr(al, "_known_failures", lambda domain: [_failure(zinc=4.0)])

    picked = suggest_next_experiments(_plan(), [], n_suggest=1)
    assert picked[0].natural["Zinc phosphate"] != 4.0


def test_selection_unchanged_when_nothing_has_failed(monkeypatch):
    import app.services.active_learning as al

    calls: list[float] = []

    def _score(natural, *a, **k):
        calls.append(natural["Zinc phosphate"])
        return (natural["Zinc phosphate"] * 10.0, 1.0)

    monkeypatch.setattr(al, "_surrogate_score", _score)
    monkeypatch.setattr(al, "_known_failures", lambda domain: [])

    picked = suggest_next_experiments(_plan(), [], n_suggest=1)
    assert picked[0].natural["Zinc phosphate"] == 12.0  # highest acquisition wins
    assert calls


def test_failure_lookup_degrades_when_the_store_is_unavailable(monkeypatch):
    """A broken measurement store must not empty the candidate set."""
    import app.services.active_learning as al
    import app.services.failure_memory as fm

    def _boom(*a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr(fm, "failed_records", _boom)
    assert al._known_failures(ProductDomain.anticorrosion_coating) == []


# ── it only engages once results carry limits ────────────────────────────────


def test_measurements_without_spec_limits_are_not_failures():
    """A bare number has no verdict — it must not be treated as a failure."""
    assert Measurement(metric="salt_spray_hours", value=300.0).passed is None

"""Tests for objective contract normalization and validation."""
from __future__ import annotations

import pytest

from app.domain.objective_contract import (
    normalize_objective,
    normalize_objectives,
    objective_metrics,
    row_has_required_measurements,
    validate_measurements,
)
from app.domain.schemas import ObjectiveSpec, ProductDomain, Requirement


def test_legacy_objective_without_id_gets_metric_as_id():
    obj = normalize_objective(ObjectiveSpec(metric="salt_spray_hours", weight=1.0, direction="maximize"))
    assert obj.id == "salt_spray_hours"
    assert obj.display_name
    assert obj.unit == "h"


def test_normalize_objectives_fills_defaults_from_requirement():
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        objectives=[ObjectiveSpec(metric="cost_cny_per_kg", weight=0.3, direction="minimize")],
    )
    objs = normalize_objectives(req)
    assert len(objs) == 1
    assert objs[0].unit == "CNY/kg"


def test_validate_measurements_drops_unknown_keys():
    objs = [ObjectiveSpec(metric="salt_spray_hours", weight=1.0, direction="maximize")]
    out = validate_measurements({"salt_spray_hours": 500, "bogus": 1}, objs)
    assert out == {"salt_spray_hours": 500}


def test_validate_measurements_strict_raises():
    objs = [ObjectiveSpec(metric="salt_spray_hours", weight=1.0, direction="maximize")]
    with pytest.raises(ValueError, match="Unknown measurement"):
        validate_measurements({"unknown": 1}, objs, strict=True)


def test_row_completed_requires_primary_metric():
    objs = [
        ObjectiveSpec(metric="salt_spray_hours", weight=0.5, direction="maximize"),
        ObjectiveSpec(metric="cost_cny_per_kg", weight=0.5, direction="minimize"),
    ]
    assert row_has_required_measurements({"salt_spray_hours": 800}, objs)
    assert not row_has_required_measurements({"cost_cny_per_kg": 10}, objs)
    assert row_has_required_measurements(
        {"salt_spray_hours": 800, "cost_cny_per_kg": 10}, objs, require_all=True
    )


def test_objective_metrics_order():
    objs = normalize_objectives(
        Requirement(
            domain=ProductDomain.degreaser,
            objectives=[
                ObjectiveSpec(metric="cleaning_efficiency", weight=0.5, direction="maximize"),
                ObjectiveSpec(metric="voc_gpl", weight=0.5, direction="minimize"),
            ],
        )
    )
    assert objective_metrics(objs) == ["cleaning_efficiency", "voc_gpl"]

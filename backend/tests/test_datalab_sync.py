"""C-5: Datalab ELN -> measurement_store sync (mock-tested).

The sync CODE path is verified here with httpx.MockTransport. End-to-end
validation against a real Datalab stays BLOCKED (no reachable URL) —
``validated`` is always None in these reports by design.
"""
from __future__ import annotations

import httpx
import pytest

from app.db.database import Base, make_engine, make_session_factory
from app.services import datalab_sync
from app.services.datalab_sync import (
    MEASUREMENT_BLOCK_ID,
    extract_measurements,
    sync_datalab_to_store,
)


def _item_envelope(measurements: list[dict] | None) -> dict:
    blocks = {}
    if measurements is not None:
        blocks[MEASUREMENT_BLOCK_ID] = {
            "block_id": MEASUREMENT_BLOCK_ID,
            "blocktype": "comment",
            "data": {"experiment_id": 7, "measurements": measurements},
        }
    return {
        "item_id": "test:ABC123",
        "item_data": {"blocks_obj": blocks},
    }


def _transport_for(body: dict | None, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/items/test:ABC123/"
        if body is None:
            return httpx.Response(status, json={})
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    import app.db.database as db_mod
    from app.db.models import ExperimentRow

    engine = make_engine(f"sqlite:///{tmp_path}/datalab_sync.db")
    Base.metadata.create_all(engine)
    fac = make_session_factory(engine)
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: fac)
    # measurements.measurement FK -> experiments.id: seed one experiment.
    with fac() as session:
        session.add(ExperimentRow(domain="coating", measured={}, source="lab"))
        session.commit()
    return fac


_MEAS = [
    {
        "metric": "salt_spray_hours",
        "value": 720.0,
        "unit": "h",
        "test_method": "ASTM B117",
        "spec_min": 500.0,
    }
]


def test_sync_happy_path(factory):
    t = _transport_for(_item_envelope(_MEAS))
    report = sync_datalab_to_store(
        "http://datalab:5001", "test:ABC123", 1,
        session_factory=factory, _transport=t,
    )
    assert report["synced"] == 1
    assert report["errors"] == []
    assert report["validated"] is None  # honest: mock run, not real validation

    from app.db.measurement_store import MeasurementStore

    stored = MeasurementStore(factory).for_experiment(1)
    assert len(stored) == 1
    assert stored[0].metric == "salt_spray_hours"
    assert stored[0].value == 720.0
    assert stored[0].test_method == "ASTM B117"


def test_sync_no_block_is_skipped_not_error(factory):
    t = _transport_for(_item_envelope(None))
    report = sync_datalab_to_store(
        "http://datalab:5001", "test:ABC123", 1,
        session_factory=factory, _transport=t,
    )
    assert report["synced"] == 0
    assert report["skipped"] == 1
    assert report["errors"] == []


def test_sync_api_failure_is_fail_open(factory):
    t = _transport_for(None, status=500)
    report = sync_datalab_to_store(
        "http://datalab:5001", "test:ABC123", 1,
        session_factory=factory, _transport=t,
    )
    assert report["synced"] == 0
    assert report["errors"] != []


def test_extract_skips_malformed_measurements():
    item_data = {
        "blocks_obj": {
            MEASUREMENT_BLOCK_ID: {
                "data": {
                    "measurements": [
                        {"metric": "ok", "value": 1.0},
                        {"metric": "bad-no-value"},
                        "not-a-dict",
                    ]
                }
            }
        }
    }
    out = extract_measurements(item_data)
    assert [m.metric for m in out] == ["ok"]


def test_extract_empty_when_no_blocks():
    assert extract_measurements({}) == []
    assert extract_measurements({"blocks_obj": "nope"}) == []

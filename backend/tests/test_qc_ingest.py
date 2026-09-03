"""Tests for qc_ingest measurement sync failure transparency (A2)."""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock


def test_sync_measurements_failure_returns_error_marker(monkeypatch):
    """A DB write failure must surface via ``_sync_error`` rather than a bare
    ``{}`` that callers mistake for 'no measurements' (A2)."""
    from app.services import qc_ingest
    import app.db.session_utils as su

    @contextmanager
    def _boom(*a, **k):
        raise RuntimeError("db down")
        yield  # noqa: unreachable

    monkeypatch.setattr(su, "commit_session", _boom)
    # also patch the already-imported reference if any, and qc_ingest re-imports inside fn so su patch suffices
    # but also patch via qc_ingest's lazy import path: patch database factory to avoid real DB
    class _MS:
        def for_experiment(self, eid):
            return [type("M", (), {"metric": "x", "value": 1.0})()]

    monkeypatch.setattr("app.db.measurement_store.get_measurement_store", lambda: _MS())

    out = qc_ingest.sync_measurements_to_experiment(123)
    assert "_sync_error" in out
    assert out.get("experiment_id") == 123


def test_sync_measurements_no_data_returns_empty_dict(monkeypatch):
    """No measurements is still a clean empty dict (success path unchanged)."""
    from app.services import qc_ingest

    class _MS:
        def for_experiment(self, eid):
            return []

    monkeypatch.setattr("app.db.measurement_store.get_measurement_store", lambda: _MS())

    out = qc_ingest.sync_measurements_to_experiment(123)
    assert out == {}
    assert "_sync_error" not in out

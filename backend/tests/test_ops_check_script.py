"""``scripts/ops_check.py`` reads the API-only diagnostics in one go (round-3 P2-6)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ops_check.py"

pytestmark = pytest.mark.skipif(not SCRIPT.is_file(), reason="scripts/ not present (backend-only checkout)")


@pytest.fixture(scope="module")
def ops():
    spec = importlib.util.spec_from_file_location("ops_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def api_fetch():
    client = TestClient(app)

    def fetch(path: str):
        response = client.get(path)
        response.raise_for_status()
        return response.json()

    return fetch


def test_every_probe_is_a_real_route(ops):
    """A probe pointing at a renamed route would turn the sweep into a permanent 'UNREADABLE'."""
    from tests.test_frontend_api_wiring import API_ONLY_ROUTES, _documented_routes

    documented = _documented_routes()
    for title, path in ops.PROBES:
        route = path.split("?", 1)[0]
        assert route in documented, f"{title}: {route} is not a documented route"
        assert route in API_ONLY_ROUTES, f"{route} is now called by a screen — the script may not be needed"


def test_a_healthy_deployment_reads_clean(ops, api_fetch):
    lines, status = ops.run_checks(api_fetch, strict=True)
    text = "\n".join(lines)
    assert status == 0, text
    for title, _path in ops.PROBES:
        assert f"== {title}" in text
    assert "UNREADABLE" not in text and text.rstrip().endswith("OK")


def test_one_unreadable_endpoint_does_not_hide_the_others(ops, api_fetch):
    def flaky(path: str):
        if path.startswith("/api/ops/evidence-stats"):
            raise RuntimeError("boom")
        return api_fetch(path)

    lines, status = ops.run_checks(flaky)
    text = "\n".join(lines)
    assert status == 1
    assert "UNREADABLE: RuntimeError: boom" in text
    assert "== DataLab orphan cleanup" in text  # later probes still ran


def test_dead_orphans_are_a_finding_only_in_strict_mode(ops, api_fetch):
    def with_dead_orphans(path: str):
        if path.endswith("/datalab-orphans"):
            return {"counts": {"DEAD": 2, "PENDING": 1}, "items": []}
        return api_fetch(path)

    relaxed_lines, relaxed = ops.run_checks(with_dead_orphans)
    strict_lines, strict = ops.run_checks(with_dead_orphans, strict=True)
    assert relaxed == 0 and strict == 2
    text = "\n".join(strict_lines)
    assert "2 DataLab sample(s) could not be deleted" in text
    assert "1 sample(s) still queued" in text  # the nudge to retry, in both modes
    assert "FINDINGS" in "\n".join(relaxed_lines)  # reported either way; strict only changes the status


def test_an_unavailable_kb_index_is_a_finding(ops, api_fetch):
    def broken_index(path: str):
        if path.endswith("/kb-health"):
            return {"available": False}
        return api_fetch(path)

    _lines, status = ops.run_checks(broken_index, strict=True)
    assert status == 2

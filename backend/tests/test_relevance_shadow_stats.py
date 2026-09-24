"""Relevance-shadow persistence + stats API (W1 calibration)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.domain.schemas import Evidence
from app.main import app


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.delenv("FORMUMIND_DATA_DIR", raising=False)
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()

    shadow_path = tmp_path / "kb_relevance_shadow.jsonl"
    monkeypatch.setattr(
        "app.services.kb_ingest_audit._shadow_jsonl_path",
        lambda: shadow_path,
    )
    # Avoid SQLite audit noise when DB tables may be missing in unit fixtures.
    monkeypatch.setattr(
        "app.services.kb_ingest_audit._try_sqlite",
        lambda _row: False,
    )
    yield
    get_settings.cache_clear()


def test_shadow_batch_persists_and_stats_aggregate(monkeypatch):
    from app.services import kb_ingest
    from app.services.kb_ingest_audit import load_relevance_shadow_stats

    monkeypatch.setattr(get_settings(), "kb_relevance_shadow", True, raising=False)
    monkeypatch.setattr(get_settings(), "kb_project_source_quota", 0, raising=False)
    monkeypatch.setattr(get_settings(), "kb_ingest_min_relevance", 0.45, raising=False)

    rows = [
        Evidence(
            source="OpenAlex",
            identifier="10.1000/on",
            title="Magnesium alloy passivation chrome-free",
            snippet="conversion coating corrosion",
            relevance=0.99,
        ),
        Evidence(
            source="OpenAlex",
            identifier="10.1000/off",
            title="Quantum computing qubits",
            snippet="entanglement superconducting",
            relevance=0.98,
        ),
    ]
    targets = kb_ingest.select_ingest_targets(
        rows,
        project_id="p-shadow",
        query="magnesium passivation chrome-free",
        skip_topic_filter=True,
        write_audit=False,
    )
    assert len(targets) == 2, "shadow must not drop rows"

    stats = load_relevance_shadow_stats(limit=10)
    assert stats["batches"] >= 1
    assert stats["samples"] >= 2
    assert "would_reject" in stats
    assert stats["recent"]
    last = stats["recent"][0]
    assert last["n"] == 2
    assert last["would_reject"] >= 1  # off-topic should be below threshold


def test_relevance_shadow_stats_endpoint():
    from app.services.kb_ingest_audit import record_relevance_shadow_batch

    record_relevance_shadow_batch(
        scores=[0.1, 0.2, 0.8],
        threshold=0.45,
        project_id="p1",
        query="epoxy coating",
        shadow_only_reject=2,
        topic_only_reject=0,
        both_reject=0,
    )
    client = TestClient(app)
    r = client.get("/api/kb/relevance-shadow/stats?limit=20")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["batches"] >= 1
    assert body["would_reject"] >= 2
    assert body["shadow_only_reject"] >= 2

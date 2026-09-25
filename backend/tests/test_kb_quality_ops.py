"""Batch D: KB quality-ops aggregate API."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services.kb_quality_ops import compute_kb_quality_score, build_quality_ops


def test_compute_kb_quality_score_bounds():
    out = compute_kb_quality_score(
        sources_active=10,
        sources_archived=2,
        embedded_chunks=80,
        chunks_active=100,
        scan_pressure=0.2,
        topicality_reject_pct=10.0,
        fulltext_fail_pct=None,
        vector_mode="semantic",
    )
    assert 0 <= out["score"] <= 100
    assert "embed_coverage" in out["components"]
    assert out["components"]["vector_mode"] == 10.0


def test_build_quality_ops_shape(monkeypatch):
    monkeypatch.setattr(
        "app.services.kb_index.kb_stats",
        lambda: {
            "sources_active": 5,
            "sources_archived": 1,
            "chunks_active": 50,
            "chunks_archived": 5,
            "embedded_chunks": 40,
            "scan_limit": 5000,
            "scan_pressure": 0.01,
            "scan_near_cap": False,
            "vector_mode": "keyword",
            "vector_hint": "",
            "quality_gate_drops": {"ingest": {"blocked_domain": 1}},
        },
    )
    monkeypatch.setattr(
        "app.services.kb_ingest_audit.load_relevance_shadow_stats",
        lambda limit=50: {
            "batches": 2,
            "samples": 20,
            "would_reject": 4,
            "would_reject_pct": 20.0,
            "recent": [],
        },
    )
    payload = build_quality_ops(project_id="p1")
    assert payload["project_id"] == "p1"
    assert payload["kb_quality_score"] >= 0
    assert payload["topicality_would_reject_pct"] == 20.0
    assert payload["relevance_shadow"]["batch_count"] == 2
    assert payload["notes"]


def test_quality_ops_endpoint():
    client = TestClient(app)
    r = client.get("/api/kb/quality-ops")
    assert r.status_code == 200
    body = r.json()
    assert "kb_quality_score" in body
    assert "scan_pressure" in body
    assert "relevance_shadow" in body

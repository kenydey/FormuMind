"""Tests for extended ingest endpoints."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_ingest_text():
    r = client.post(
        "/api/ingest/text",
        json={"text": "Epoxy coating formulation with zinc phosphate inhibitor.\n\nSalt spray resistance improves.", "title": "Notes"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert body["evidence"][0]["source"] == "pasted"


def test_ingest_batch():
    payload = [
        ("files", ("a.txt", b"Hello world from batch file one with enough length.", "text/plain")),
        ("files", ("b.txt", b"Second batch file about corrosion inhibitors and epoxy.", "text/plain")),
    ]
    r = client.post("/api/ingest/batch", files=payload)
    assert r.status_code == 200
    assert r.json()["files_processed"] == 2
    assert r.json()["total"] >= 1


def test_ingest_url_rejects_localhost():
    r = client.post("/api/ingest/url", json={"url": "http://127.0.0.1/secret"})
    assert r.status_code == 400


def test_ingest_url_invalid_scheme():
    r = client.post("/api/ingest/url", json={"url": "file:///etc/passwd"})
    assert r.status_code == 400

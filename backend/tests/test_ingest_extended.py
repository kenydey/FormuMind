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
    import uuid

    from tests.test_ingest_async_helpers import poll_task

    suffix = uuid.uuid4().hex[:8]
    payload = [
        (
            "files",
            (
                f"a-{suffix}.txt",
                f"Hello world from batch file one with enough length {suffix}.".encode(),
                "text/plain",
            ),
        ),
        (
            "files",
            (
                f"b-{suffix}.txt",
                f"Second batch file about corrosion inhibitors and epoxy {suffix}.".encode(),
                "text/plain",
            ),
        ),
    ]
    r = client.post("/api/ingest/batch", files=payload)
    assert r.status_code == 202, r.text
    body = poll_task(client, r.json()["task_id"])
    assert body["state"] == "completed"
    result = body.get("result") or {}
    assert result.get("files_processed") == 2
    assert result.get("total", 0) >= 1


def test_ingest_url_rejects_localhost():
    r = client.post("/api/ingest/url", json={"url": "http://127.0.0.1/secret"})
    assert r.status_code == 400


def test_ingest_url_invalid_scheme():
    r = client.post("/api/ingest/url", json={"url": "file:///etc/passwd"})
    assert r.status_code == 400

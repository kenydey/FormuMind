"""SSRF hardening tests for ingest URL fetch."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.ingestion import _is_safe_url


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


client = TestClient(app)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/secret",
        "http://127.1/admin",
        "http://2130706433/",
        "http://0177.0.0.1/",
        "http://[::1]/",
        "http://localhost/",
        "http://0.0.0.0/",
        "http://10.0.0.1/internal",
        "http://169.254.169.254/latest/meta-data/",
    ],
)
def test_is_safe_url_blocks_private_and_loopback(url: str):
    assert _is_safe_url(url) is False


def test_is_safe_url_allows_public_https():
    assert _is_safe_url("https://example.com/article") is True


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/secret",
        "http://127.1/admin",
        "http://[::1]/",
    ],
)
def test_ingest_url_endpoint_rejects_ssrf(url: str):
    r = client.post("/api/ingest/url", json={"url": url})
    assert r.status_code == 400

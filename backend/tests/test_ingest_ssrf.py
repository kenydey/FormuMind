"""SSRF hardening tests for ingest URL fetch."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.ingestion import _fetch_public_url, _is_safe_url


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


def _patch_transport(monkeypatch, handler):
    import httpx

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", patched)


def test_fetch_public_url_blocks_redirect_to_loopback(monkeypatch):
    """Regression: an external site must not be able to 302 into localhost."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
        raise AssertionError(f"loopback target must never be fetched: {request.url}")

    _patch_transport(monkeypatch, handler)
    with pytest.raises(ValueError, match="public http"):
        _fetch_public_url("https://example.com/start")


def test_fetch_public_url_follows_safe_redirects(monkeypatch):
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(200, text="ok", headers={"content-type": "text/plain"})

    _patch_transport(monkeypatch, handler)
    resp = _fetch_public_url("https://example.com/start")
    assert resp.status_code == 200
    assert resp.text == "ok"


def test_fetch_public_url_caps_redirect_count(monkeypatch):
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/loop"})

    _patch_transport(monkeypatch, handler)
    with pytest.raises(ValueError, match="Too many redirects"):
        _fetch_public_url("https://example.com/start")

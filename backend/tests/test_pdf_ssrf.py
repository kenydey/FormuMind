"""SSRF guards on OA PDF download and OpenAlex content key transport."""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.services import fulltext_fetcher as ft
from app.services.pdf_downloader import fetch_pdf_ex


class _FakeResp:
    def __init__(self, status_code: int, *, headers: dict | None = None, content: bytes = b""):
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content


def test_fetch_pdf_ex_blocks_private_initial_url():
    data, reason = fetch_pdf_ex("http://127.0.0.1/secret.pdf")
    assert data is None
    assert reason == "ssrf"


def test_fetch_pdf_ex_blocks_redirect_to_loopback(monkeypatch):
    calls: list[str] = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            calls.append(url)
            if url.startswith("https://example.com"):
                return _FakeResp(302, headers={"location": "http://127.0.0.1/secret.pdf"})
            return _FakeResp(200, headers={"content-type": "application/pdf"}, content=b"%PDF")

    monkeypatch.setattr(httpx, "Client", _Client)
    data, reason = fetch_pdf_ex("https://example.com/paper.pdf")
    assert data is None
    assert reason == "ssrf"
    assert calls == ["https://example.com/paper.pdf"]


def test_openalex_content_uses_bearer_not_query(monkeypatch):
    captured: dict = {}

    class _Client:
        def __init__(self, *a, **k):
            captured["headers"] = dict(k.get("headers") or {})
            captured["follow_redirects"] = k.get("follow_redirects")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            captured["url"] = url
            captured["params"] = params
            return _FakeResp(404, headers={"content-type": "text/plain"})

    monkeypatch.setattr(httpx, "Client", _Client)
    monkeypatch.setattr(
        ft,
        "get_settings",
        lambda: type(
            "S",
            (),
            {
                "openalex_api_key": "secret-key-xyz",
                "openalex_content_enabled": True,
            },
        )(),
    )
    monkeypatch.setattr(ft, "_openalex_work_id", lambda *a, **k: "W123")
    from app.domain.schemas import Evidence

    ev = Evidence(
        source="OpenAlex",
        identifier="10.1000/example",
        title="t",
        snippet="s",
        relevance=1.0,
    )
    assert ft._openalex_content_text(ev, timeout=1.0) is None
    assert captured.get("params") in (None, {})
    auth = captured.get("headers", {}).get("Authorization", "")
    assert auth == "Bearer secret-key-xyz"
    assert "api_key=" not in (captured.get("url") or "")
    assert captured.get("follow_redirects") is False

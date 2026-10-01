"""P1-2: PDF 下载链路三件套。

Contract under test (all offline — httpx.Client is stubbed):
1. Disk cache: a repeat URL is served from disk (reason "cache") without a
   second network request; TTL expiry re-fetches.
2. 403 host blocklist: after one 403 from a host, further URLs on the same
   host short-circuit with "blocked:403_host" (caller keeps its "try next
   mirror candidate" semantics).
3. Retry: timeout / 5xx are retried with backoff up to kb_pdf_retry_attempts
   total attempts; 4xx (other than the 403 path) is not retried.
"""
from __future__ import annotations

import time

import httpx
import pytest

from app.config import get_settings
from app.services import pdf_downloader as pd

_PDF = b"%PDF-1.4 fake-bytes-for-tests"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    settings = get_settings()
    # Isolate the disk cache per test.
    monkeypatch.setattr(settings, "kb_pdf_cache_enabled", True)
    monkeypatch.setattr(settings, "kb_pdf_cache_dir", str(tmp_path / "pdfcache"))
    monkeypatch.setattr(settings, "kb_pdf_cache_ttl_s", 3600)
    monkeypatch.setattr(settings, "kb_pdf_retry_attempts", 2)
    monkeypatch.setattr(settings, "kb_403_blocklist_ttl_s", 600)
    # Sandbox DNS is unreliable; the SSRF guard itself is covered by
    # test_ingest_ssrf.py — here we only test cache/blocklist/retry.
    import app.services.ingestion as ingestion_mod

    monkeypatch.setattr(ingestion_mod, "_is_safe_url", lambda url: True)
    pd._host_403_until.clear()
    yield
    get_settings.cache_clear()
    pd._host_403_until.clear()


class _Resp:
    def __init__(self, status_code=200, content=_PDF, content_type="application/pdf"):
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": content_type}


class _ScriptedClient:
    """httpx.Client stub driven by a per-test script of outcomes."""

    script: list = []  # each item: _Resp | Exception
    calls: list = []

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, *a, **kw):
        type(self).calls.append(str(url))
        outcome = type(self).script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture()
def client(monkeypatch):
    _ScriptedClient.script = []
    _ScriptedClient.calls = []
    monkeypatch.setattr(httpx, "Client", _ScriptedClient)
    return _ScriptedClient


# ── 1. disk cache ─────────────────────────────────────────────────────────────


def test_second_fetch_served_from_cache(client):
    url = "https://example.com/a.pdf"
    client.script = [_Resp()]
    data, reason = pd.fetch_pdf_ex(url, timeout=1)
    assert data == _PDF and reason == "ok"

    client.script = [_Resp()]  # would be consumed only on a network hit
    data2, reason2 = pd.fetch_pdf_ex(url, timeout=1)
    assert data2 == _PDF and reason2 == "cache"
    assert len(client.calls) == 1  # no second network request


def test_cache_ttl_expiry_refetches(client):
    from pathlib import Path

    url = "https://example.com/b.pdf"
    client.script = [_Resp()]
    pd.fetch_pdf_ex(url, timeout=1)

    # Age the cached file past the TTL.
    cache_file = next(Path(get_settings().kb_pdf_cache_dir).glob("*.pdf"))
    old = time.time() - 7200
    import os

    os.utime(cache_file, (old, old))

    client.script = [_Resp(content=b"%PDF-1.4 refreshed")]
    data, reason = pd.fetch_pdf_ex(url, timeout=1)
    assert reason == "ok" and data == b"%PDF-1.4 refreshed"
    assert len(client.calls) == 2


def test_cache_disabled_skips_disk(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "kb_pdf_cache_enabled", False)
    client.script = [_Resp(), _Resp()]
    pd.fetch_pdf_ex("https://example.com/c.pdf", timeout=1)
    data, reason = pd.fetch_pdf_ex("https://example.com/c.pdf", timeout=1)
    assert reason == "ok"  # no "cache" hit
    assert len(client.calls) == 2


# ── 2. 403 host blocklist ─────────────────────────────────────────────────────


def test_403_blocklists_host_for_batch(client):
    client.script = [_Resp(status_code=403, content=b"forbidden", content_type="text/html")]
    data, reason = pd.fetch_pdf_ex("https://wall.example.com/x1.pdf", timeout=1)
    assert data is None and reason == "status:403"

    # Same host, different URL: no network request at all.
    data2, reason2 = pd.fetch_pdf_ex("https://wall.example.com/x2.pdf", timeout=1)
    assert data2 is None and reason2 == "blocked:403_host"
    assert len(client.calls) == 1

    # Different host is unaffected.
    client.script = [_Resp()]
    data3, reason3 = pd.fetch_pdf_ex("https://fine.example.org/y.pdf", timeout=1)
    assert reason3 == "ok"


def test_blocklist_expires(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "kb_403_blocklist_ttl_s", 1)
    client.script = [_Resp(status_code=403, content_type="text/html")]
    pd.fetch_pdf_ex("https://wall2.example.com/x.pdf", timeout=1)
    assert pd._host_403_blocklisted("wall2.example.com")

    time.sleep(1.1)
    client.script = [_Resp()]
    data, reason = pd.fetch_pdf_ex("https://wall2.example.com/x.pdf", timeout=1)
    assert reason == "ok" and data == _PDF


# ── 3. retry ──────────────────────────────────────────────────────────────────


def test_timeout_retried_then_succeeds(client, monkeypatch):
    monkeypatch.setattr(pd, "_retry_delay_s", lambda attempt: 0)  # no sleeping in tests
    client.script = [httpx.TimeoutException("slow"), _Resp()]
    data, reason = pd.fetch_pdf_ex("https://example.com/d.pdf", timeout=1)
    assert reason == "ok" and data == _PDF
    assert len(client.calls) == 2


def test_5xx_retried_then_succeeds(client, monkeypatch):
    monkeypatch.setattr(pd, "_retry_delay_s", lambda attempt: 0)
    client.script = [_Resp(status_code=503, content_type="text/html"), _Resp()]
    data, reason = pd.fetch_pdf_ex("https://example.com/e.pdf", timeout=1)
    assert reason == "ok"
    assert len(client.calls) == 2


def test_retry_exhausted_reports_last_reason(client, monkeypatch):
    monkeypatch.setattr(pd, "_retry_delay_s", lambda attempt: 0)
    client.script = [httpx.TimeoutException("s1"), httpx.TimeoutException("s2")]
    data, reason = pd.fetch_pdf_ex("https://example.com/f.pdf", timeout=1)
    assert data is None and reason == "timeout"
    assert len(client.calls) == 2


def test_no_retry_when_attempts_is_1(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "kb_pdf_retry_attempts", 1)
    client.script = [httpx.TimeoutException("s1"), _Resp()]
    data, reason = pd.fetch_pdf_ex("https://example.com/g.pdf", timeout=1)
    assert reason == "timeout"
    assert len(client.calls) == 1


def test_404_not_retried(client, monkeypatch):
    monkeypatch.setattr(pd, "_retry_delay_s", lambda attempt: 0)
    client.script = [_Resp(status_code=404, content_type="text/html"), _Resp()]
    data, reason = pd.fetch_pdf_ex("https://example.com/h.pdf", timeout=1)
    assert reason == "status:404"
    assert len(client.calls) == 1

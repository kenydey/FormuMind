"""B-8: provider 结构化健康事件 + 熔断器 + 指数退避。"""
from __future__ import annotations

import time

import httpx
import pytest

from app.services import provider_health as ph
from app.services.provider_health import (
    backoff_delay,
    classify_error,
    provider_breaker_open,
    provider_health_snapshot,
    recent_provider_events,
    record_provider_failure,
    record_provider_success,
    reset_provider_health,
)


@pytest.fixture(autouse=True)
def _clean():
    reset_provider_health()
    yield
    reset_provider_health()


# ── classify_error ────────────────────────────────────────────────

class _Resp:
    def __init__(self, code):
        self.status_code = code


class _HTTPStatusError(Exception):
    def __init__(self, code):
        self.response = _Resp(code)
        super().__init__(f"status {code}")


def test_classify_timeout():
    assert classify_error(httpx.ConnectTimeout("timed out")) == "timeout"


def test_classify_rate_limited():
    assert classify_error(_HTTPStatusError(429)) == "rate_limited"
    assert classify_error(RuntimeError("429 rate limit exceeded")) == "rate_limited"


def test_classify_http_5xx():
    assert classify_error(_HTTPStatusError(503)) == "http_5xx"


def test_classify_connection():
    assert classify_error(httpx.ConnectError("boom")) == "connection"


def test_classify_auth():
    assert classify_error(_HTTPStatusError(401)) == "auth"
    assert classify_error(_HTTPStatusError(403)) == "auth"


def test_classify_other():
    assert classify_error(ValueError("weird")) == "other"


def test_detail_sanitizes_api_key():
    exc = RuntimeError("request failed https://x.test/?api_key=SECRET123&q=a")
    kind = record_provider_failure("openalex", exc, threshold=100)
    assert kind == "other"
    ev = recent_provider_events(1)[0]
    assert "SECRET123" not in ev["detail"]
    assert "api_key" not in ev["detail"]
    assert "https://x.test/" in ev["detail"]


# ── 熔断器 ────────────────────────────────────────────────────────

def test_breaker_opens_after_threshold():
    for _ in range(4):
        record_provider_failure("tavily", RuntimeError("x"), threshold=5, cooldown_sec=300)
        assert not provider_breaker_open("tavily", threshold=5, cooldown_sec=300)
    record_provider_failure("tavily", RuntimeError("x"), threshold=5, cooldown_sec=300)
    assert provider_breaker_open("tavily", threshold=5, cooldown_sec=300)


def test_success_resets_consecutive_failures():
    for _ in range(4):
        record_provider_failure("tavily", RuntimeError("x"), threshold=5, cooldown_sec=300)
    record_provider_success("tavily", time.monotonic())
    record_provider_failure("tavily", RuntimeError("x"), threshold=5, cooldown_sec=300)
    assert not provider_breaker_open("tavily", threshold=5, cooldown_sec=300)
    snap = provider_health_snapshot()
    assert snap["tavily"]["consecutive_failures"] == 1
    assert snap["tavily"]["breaker_open"] is False


def test_breaker_closes_after_cooldown(monkeypatch):
    record_provider_failure("openalex", RuntimeError("x"), threshold=1, cooldown_sec=60)
    assert provider_breaker_open("openalex", threshold=1, cooldown_sec=60)
    # 快进 61 秒 → 冷却结束，半开（下一次真实调用即探测）。
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 61)
    assert not provider_breaker_open("openalex", threshold=1, cooldown_sec=60)


def test_events_ring_buffer_capped():
    for i in range(250):
        record_provider_failure("openalex", RuntimeError(f"e{i}"), threshold=10**9)
    assert len(recent_provider_events(500)) == 200
    # 新 → 旧
    assert recent_provider_events(1)[0]["detail"].endswith("e249")


# ── backoff_delay ─────────────────────────────────────────────────

def test_backoff_grows_exponentially():
    d0 = [backoff_delay(0) for _ in range(20)]
    d1 = [backoff_delay(1) for _ in range(20)]
    d2 = [backoff_delay(2) for _ in range(20)]
    assert all(1.0 <= x <= 1.5 for x in d0)
    assert all(2.0 <= x <= 2.5 for x in d1)
    assert all(4.0 <= x <= 4.5 for x in d2)
    assert backoff_delay(99) <= 30.5  # cap


# ── search_providers 接线 ─────────────────────────────────────────

def _boom_client(*, exc):
    class FakeClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            raise exc

    return FakeClient


def test_openalex_failure_records_structured_event(monkeypatch):
    from app.services.search_providers import search_openalex

    monkeypatch.setattr(
        "app.services.search_providers.httpx.Client",
        _boom_client(exc=httpx.ConnectError("dns down")),
    )
    hits = search_openalex("zinc coating", limit=5)
    assert hits == []
    ev = recent_provider_events(1)[0]
    assert ev["provider"] == "openalex"
    assert ev["outcome"] == "error"
    assert ev["error_kind"] == "connection"


def test_breaker_open_skips_http_call(monkeypatch):
    from app.services import search_providers as sp
    from app.services.search_providers import search_openalex

    monkeypatch.setattr(sp, "_breaker_settings", lambda settings: (2, 300.0))
    monkeypatch.setattr(
        "app.services.search_providers.httpx.Client",
        _boom_client(exc=httpx.ConnectError("dns down")),
    )
    assert search_openalex("zinc coating", limit=5) == []
    assert search_openalex("zinc coating", limit=5) == []
    # 第三次：熔断已开，不再发起 HTTP（换一个必炸的 client 也无妨——
    # 用计数 client 验证 get 未被调用）。
    calls = []

    class CountingClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            calls.append(url)
            raise AssertionError("breaker open but HTTP was attempted")

    monkeypatch.setattr("app.services.search_providers.httpx.Client", CountingClient)
    assert search_openalex("zinc coating", limit=5) == []
    assert calls == []
    ev = recent_provider_events(1)[0]
    assert ev["outcome"] == "breaker_open_skip"


def test_get_with_retry_uses_exponential_backoff(monkeypatch):
    from app.services.search_providers import _get_with_retry

    sleeps = []
    monkeypatch.setattr("app.services.search_providers.time.sleep", sleeps.append)

    class Resp429:
        status_code = 429

        def json(self):
            return {}  # 无 retryAfter → 走指数退避

    class Resp200:
        status_code = 200

    responses = [Resp429(), Resp429(), Resp200()]

    class FakeClient:
        def get(self, url, params=None):
            return responses.pop(0)

    assert _get_with_retry(FakeClient(), "https://x", {}) is not None
    assert len(sleeps) == 2
    assert 1.0 <= sleeps[0] <= 1.5
    assert 2.0 <= sleeps[1] <= 2.5


def test_get_with_retry_honours_retry_after(monkeypatch):
    from app.services.search_providers import _get_with_retry

    sleeps = []
    monkeypatch.setattr("app.services.search_providers.time.sleep", sleeps.append)

    class Resp429:
        status_code = 429

        def json(self):
            return {"retryAfter": 7}

    class Resp200:
        status_code = 200

    responses = [Resp429(), Resp200()]

    class FakeClient:
        def get(self, url, params=None):
            return responses.pop(0)

    _get_with_retry(FakeClient(), "https://x", {})
    assert sleeps == [7.0]

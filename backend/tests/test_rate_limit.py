"""Rate-limit rule matching tests."""
from __future__ import annotations

from types import SimpleNamespace

from app.middleware.rate_limit import _RATE_RULES, _rule_for


def _request(method: str, path: str):
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path))


def _rule(prefix: str) -> tuple[int, float]:
    for _method, p, limit, window in _RATE_RULES:
        if p == prefix:
            return limit, window
    raise AssertionError(f"no rule for {prefix}")


def test_stream_rule_not_shadowed_by_search_prefix():
    """Regression: /api/search/stream must use its own (stricter) rule."""
    assert _rule_for(_request("POST", "/api/search/stream")) == _rule("/api/search/stream")


def test_search_rule_still_applies_to_plain_search():
    assert _rule_for(_request("POST", "/api/search")) == _rule("/api/search")


def test_unmatched_paths_have_no_rule():
    assert _rule_for(_request("POST", "/api/chat")) is None
    assert _rule_for(_request("GET", "/api/search")) is None
